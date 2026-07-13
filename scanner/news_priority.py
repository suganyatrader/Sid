import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
STOCK_IDENTIFIERS_FILE = DATA_DIR / 'stock_identifiers.json'


def _to_number(value: Any) -> Optional[float]:
    if value in (None, '', 'None'):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_stock_identifiers(path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    target = Path(path) if path is not None else STOCK_IDENTIFIERS_FILE
    if not target.exists():
        return []

    with target.open('r', encoding='utf-8') as handle:
        payload = json.load(handle)

    if isinstance(payload, dict):
        return [item for item in payload.values() if isinstance(item, dict)]

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    return []


def load_news_payload(path: Optional[Union[str, Path]] = None, payload: Optional[Union[Dict[str, Any], str]] = None) -> Dict[str, Dict[str, Any]]:
    if payload is not None:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            try:
                loaded = json.loads(payload)
            except json.JSONDecodeError:
                return {}
            return loaded if isinstance(loaded, dict) else {}
        return {}

    if path is None:
        return {}

    target = Path(path)
    if not target.exists():
        return {}

    with target.open('r', encoding='utf-8') as handle:
        loaded = json.load(handle)

    if isinstance(loaded, dict):
        return loaded

    return {}


def _infer_index_relevance(symbol: str, sector: Optional[str] = None) -> float:
    symbol_upper = (symbol or '').upper()
    if symbol_upper in {
        'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'HUL', 'SBI', 'ITC', 'LT', 'BHARTIARTL',
        'AXISBANK', 'KOTAKBANK', 'MARUTI', 'SUNPHARMA', 'WIPRO', 'ASIANPAINT', 'NTPC', 'TITAN',
        'NESTLEIND', 'M&M', 'POWERGRID', 'ONGC', 'ULTRACEMCO', 'BAJAJ-AUTO', 'JSWSTEEL', 'TATASTEEL',
        'HCLTECH', 'INDUSINDBK', 'ADANIENT', 'ADANIPORTS', 'COALINDIA', 'DRREDDY', 'BPCL', 'HEROMOTOCO',
        'GRASIM', 'EICHERMOT', 'DIVISLAB', 'CIPLA', 'SBILIFE', 'BRITANNIA', 'UPL', 'IOC', 'PIDILITIND',
        'DABUR', 'PNB', 'MUTHOOTFIN', 'GODREJCP',
    }:
        return 1.0
    if sector in {'Energy', 'IT', 'Financials', 'Banking', 'Auto', 'FMCG'}:
        return 0.6
    return 0.2


def prioritize_stocks(
    stock_identifiers: Union[List[Dict[str, Any]], Dict[str, Dict[str, Any]]],
    news_payload: Optional[Dict[str, Any]] = None,
    top_n: int = 300,
) -> Dict[str, List[Dict[str, Any]]]:
    if isinstance(stock_identifiers, dict):
        items = [item for item in stock_identifiers.values() if isinstance(item, dict)]
    else:
        items = [item for item in stock_identifiers if isinstance(item, dict)]

    if top_n <= 0:
        raise ValueError('top_n must be a positive integer')

    scored_entries: List[Dict[str, Any]] = []
    for metadata in items:
        stock_id = str(metadata.get('stock_id') or metadata.get('symbol') or '').strip()
        if not stock_id:
            continue

        news_stats = news_payload.get(stock_id, {}) if isinstance(news_payload, dict) else {}
        if not isinstance(news_stats, dict):
            news_stats = {}

        positive = _to_number(news_stats.get('positive')) or 0.0
        negative = _to_number(news_stats.get('negative')) or 0.0

        if positive > negative:
            sentiment = 'buy'
            margin = positive - negative
        elif negative > positive:
            sentiment = 'short'
            margin = negative - positive
        else:
            sentiment = 'neutral'
            margin = 0.0

        liquidity_score = _to_number(metadata.get('liquidity_score')) or 0.0
        index_relevance = _infer_index_relevance(str(metadata.get('symbol') or stock_id), metadata.get('sector'))
        strength = abs(margin) * 10.0
        score = strength + (liquidity_score * 10.0) + (index_relevance * 5.0)

        scored_entries.append({
            'stock_id': stock_id,
            'symbol': metadata.get('symbol') or stock_id,
            'sector': metadata.get('sector'),
            'sentiment': sentiment,
            'positive_mentions': positive,
            'negative_mentions': negative,
            'margin': margin,
            'score': score,
        })

    buy_entries = [entry for entry in scored_entries if entry['sentiment'] == 'buy']
    short_entries = [entry for entry in scored_entries if entry['sentiment'] == 'short']
    neutral_entries = [entry for entry in scored_entries if entry['sentiment'] == 'neutral']

    buy_entries.sort(key=lambda entry: entry['score'], reverse=True)
    short_entries.sort(key=lambda entry: entry['score'], reverse=True)
    neutral_entries.sort(key=lambda entry: entry['score'], reverse=True)

    buy_output = buy_entries[:top_n]
    short_output = short_entries[:top_n]

    if len(buy_output) < top_n:
        buy_output.extend(neutral_entries[: top_n - len(buy_output)])
    if len(short_output) < top_n:
        short_output.extend(neutral_entries[: top_n - len(short_output)])

    return {
        'buy': buy_output,
        'short': short_output,
    }


def write_priority_lists(
    prioritized: Dict[str, List[Dict[str, Any]]],
    path: Optional[Union[str, Path]] = None,
) -> Path:
    target = Path(path) if path is not None else DATA_DIR / 'news_priority_lists.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(prioritized)
    payload['generated_at'] = datetime.now(timezone.utc).isoformat()
    with target.open('w', encoding='utf-8') as handle:
        json.dump(payload, handle, indent=2)
        handle.write('\n')
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Prioritize stocks from live news sentiment into buy and short lists')
    parser.add_argument('--stock-identifiers', default=str(STOCK_IDENTIFIERS_FILE), help='Path to stock identifiers JSON file')
    parser.add_argument('--news-data', default=None, help='Optional path to a JSON file containing positive/negative news counts per stock, used as an offline override. If omitted, live news is fetched from Moneycontrol RSS feeds and analyzed via Groq.')
    parser.add_argument('--output', default=str(DATA_DIR / 'news_priority_lists.json'), help='Optional path to write the ranked JSON output')
    parser.add_argument('--top-n', type=int, default=300, help='Maximum number of entries to keep in each list')
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    stock_identifiers = load_stock_identifiers(args.stock_identifiers)

    if args.news_data:
        news_payload = load_news_payload(args.news_data)
    else:
        from news_fetcher import fetch_articles
        from news_sentiment import analyze_articles

        articles = fetch_articles()
        news_payload = analyze_articles(articles, stock_identifiers)

    prioritized = prioritize_stocks(stock_identifiers, news_payload, top_n=args.top_n)
    out_path = write_priority_lists(prioritized, args.output)

    buy_entries = prioritized.get('buy', [])
    short_entries = prioritized.get('short', [])

    buy_positive_count = sum(1 for entry in buy_entries if entry.get('positive_mentions', 0) > 0)
    buy_negative_count = sum(1 for entry in buy_entries if entry.get('negative_mentions', 0) > 0)
    short_positive_count = sum(1 for entry in short_entries if entry.get('positive_mentions', 0) > 0)
    short_negative_count = sum(1 for entry in short_entries if entry.get('negative_mentions', 0) > 0)

    print(f"[news_priority] Buy list: {len(buy_entries)} stocks | positive_mentions={buy_positive_count} | negative_mentions={buy_negative_count}")
    print(f"[news_priority] Short list: {len(short_entries)} stocks | positive_mentions={short_positive_count} | negative_mentions={short_negative_count}")
    print(f'[news_priority] Wrote results to {out_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
