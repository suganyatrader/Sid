from typing import Dict, List, Optional

DEFAULT_RSS_FEEDS = [
    'https://www.moneycontrol.com/rss/latestnews.xml',
    'https://www.moneycontrol.com/rss/marketreports.xml',
    'https://www.moneycontrol.com/rss/buzzingstocks.xml',
    'https://www.moneycontrol.com/rss/results.xml',
    'https://www.moneycontrol.com/rss/economy.xml',
]


def fetch_feed(url: str, timeout: float = 10.0) -> List[Dict[str, str]]:
    try:
        import requests

        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
    except Exception as exc:
        print(f'[news_fetcher] Failed to fetch {url}: {exc}')
        return []

    try:
        import feedparser

        parsed = feedparser.parse(response.content)
    except Exception as exc:
        print(f'[news_fetcher] Failed to parse feed {url}: {exc}')
        return []

    articles = []
    for entry in getattr(parsed, 'entries', []):
        articles.append({
            'title': entry.get('title', ''),
            'summary': entry.get('summary', ''),
            'link': entry.get('link', ''),
            'published': entry.get('published', ''),
        })
    return articles


def fetch_articles(feeds: Optional[List[str]] = None, dedupe: bool = True) -> List[Dict[str, str]]:
    target_feeds = feeds if feeds is not None else DEFAULT_RSS_FEEDS

    articles: List[Dict[str, str]] = []
    for url in target_feeds:
        articles.extend(fetch_feed(url))

    if not dedupe:
        return articles

    seen = set()
    deduped: List[Dict[str, str]] = []
    for article in articles:
        key = (article.get('title', ''), article.get('link', ''))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(article)
    return deduped
