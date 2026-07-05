import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from groq_config import GroqConfig
from rate_limiter import DEFAULT_GROQ_RATE_LIMITS, MultiRateLimiter

ARTICLES_PER_BATCH = 10
SUMMARY_MAX_CHARS = 220
MIN_BATCH_SIZE = 3
RETRY_AFTER_PATTERN = re.compile(r'try again in ([\d.]+)s', re.IGNORECASE)

SYSTEM_PROMPT_TEMPLATE = (
    'You are a financial news analyst covering the Indian stock market (NSE). '
    'You will be given a list of valid NSE ticker symbols and a batch of news article '
    'headlines/summaries. For each article, determine whether it discusses a company from '
    'the ticker list, using your knowledge of Indian-listed companies to map company or brand '
    'names to the correct ticker. Only use ticker symbols from this exact list - if an '
    'article\'s subject is not in the list or you are unsure, omit it:\n\n{symbols}\n\n'
    'Respond with a single JSON object mapping each mentioned ticker symbol to '
    '{{"positive": <count>, "negative": <count>}}, counting how many of the given articles '
    'express positive vs negative sentiment about that company. Only include tickers with at '
    'least one positive or negative mention. Respond with JSON only, no other text.'
)


def _chunk(items: List[Any], size: int) -> List[List[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def build_batch_prompt(symbols: List[str], articles: List[Dict[str, str]]) -> Tuple[str, str]:
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(symbols=', '.join(symbols))

    lines = []
    for index, article in enumerate(articles, start=1):
        title = article.get('title', '').strip()
        summary = article.get('summary', '').strip()[:SUMMARY_MAX_CHARS]
        lines.append(f'{index}. {title} - {summary}')
    user_prompt = '\n'.join(lines)

    return system_prompt, user_prompt


def _merge_counts(
    target: Dict[str, Dict[str, int]], source: Dict[str, Dict[str, int]]
) -> None:
    for symbol, counts in source.items():
        if not isinstance(counts, dict):
            continue
        entry = target.setdefault(symbol, {'positive': 0, 'negative': 0})
        entry['positive'] += int(counts.get('positive', 0) or 0)
        entry['negative'] += int(counts.get('negative', 0) or 0)


def _call_groq_batch(
    client: Any,
    model: str,
    symbols: List[str],
    articles: List[Dict[str, str]],
    rate_limiter: MultiRateLimiter,
    _retried_rate_limit: bool = False,
) -> Dict[str, Dict[str, int]]:
    system_prompt, user_prompt = build_batch_prompt(symbols, articles)

    try:
        with rate_limiter:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt},
                ],
                response_format={'type': 'json_object'},
                temperature=0,
                max_tokens=1024,
            )
        content = response.choices[0].message.content
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return {}
        return parsed
    except Exception as exc:
        message = str(exc)
        is_too_large = '413' in message or 'too large' in message.lower()
        if is_too_large and len(articles) > MIN_BATCH_SIZE:
            mid = len(articles) // 2
            print(f'[news_sentiment] batch of {len(articles)} too large, splitting and retrying')
            result: Dict[str, Dict[str, int]] = {}
            for half in (articles[:mid], articles[mid:]):
                _merge_counts(
                    result,
                    _call_groq_batch(client, model, symbols, half, rate_limiter),
                )
            return result

        is_rate_limited = '429' in message or 'rate_limit_exceeded' in message
        retry_match = RETRY_AFTER_PATTERN.search(message)
        if is_rate_limited and retry_match and not _retried_rate_limit:
            delay = min(float(retry_match.group(1)), 60.0)
            print(f'[news_sentiment] rate limited, retrying in {delay:.1f}s')
            time.sleep(delay)
            return _call_groq_batch(
                client, model, symbols, articles, rate_limiter, _retried_rate_limit=True
            )

        print(f'[news_sentiment] batch failed: {exc}')
        return {}


def analyze_articles(
    articles: List[Dict[str, str]],
    stock_identifiers: List[Dict[str, Any]],
    config: Optional[GroqConfig] = None,
    articles_per_batch: int = ARTICLES_PER_BATCH,
    max_workers: int = 8,
) -> Dict[str, Dict[str, int]]:
    if config is None:
        config = GroqConfig.from_env()

    valid_symbols = {
        str(item.get('symbol') or item.get('stock_id')).strip()
        for item in stock_identifiers
        if isinstance(item, dict) and (item.get('symbol') or item.get('stock_id'))
    }

    aggregated: Dict[str, Dict[str, int]] = {}
    if not articles or not valid_symbols:
        return aggregated

    client = config.get_client()
    symbols = sorted(valid_symbols)
    batches = _chunk(articles, articles_per_batch)

    rate_limiter = MultiRateLimiter(DEFAULT_GROQ_RATE_LIMITS)
    worker_count = max(1, min(max_workers, len(batches), 16))

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(_call_groq_batch, client, config.model, symbols, batch, rate_limiter)
            for batch in batches
        ]
        for future in as_completed(futures):
            batch_result = future.result()
            for symbol, counts in batch_result.items():
                if symbol not in valid_symbols or not isinstance(counts, dict):
                    continue
                positive = int(counts.get('positive', 0) or 0)
                negative = int(counts.get('negative', 0) or 0)
                entry = aggregated.setdefault(symbol, {'positive': 0, 'negative': 0})
                entry['positive'] += positive
                entry['negative'] += negative

    return aggregated
