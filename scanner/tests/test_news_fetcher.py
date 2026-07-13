import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.append(str(Path(__file__).resolve().parents[1]))

from news_fetcher import DEFAULT_RSS_FEEDS, fetch_articles, fetch_feed


def _mock_response(content: bytes = b''):
    response = MagicMock()
    response.content = content
    response.raise_for_status = MagicMock()
    return response


def _mock_feed(entries):
    parsed = MagicMock()
    parsed.entries = entries
    return parsed


def test_fetch_feed_parses_entries():
    entries = [
        {'title': 'Reliance surges on strong Q1', 'summary': 'Profit up 20%', 'link': 'http://a', 'published': 'today'},
    ]
    with patch('requests.get', return_value=_mock_response(b'<rss></rss>')), \
         patch('feedparser.parse', return_value=_mock_feed(entries)):
        articles = fetch_feed('http://example.com/feed.xml')

    assert articles == entries


def test_fetch_feed_returns_empty_on_request_failure():
    with patch('requests.get', side_effect=Exception('network down')):
        articles = fetch_feed('http://example.com/feed.xml')

    assert articles == []


def test_fetch_feed_returns_empty_on_parse_failure():
    with patch('requests.get', return_value=_mock_response(b'garbage')), \
         patch('feedparser.parse', side_effect=Exception('bad feed')):
        articles = fetch_feed('http://example.com/feed.xml')

    assert articles == []


def test_fetch_articles_dedupes_across_feeds():
    shared_entry = {'title': 'Same headline', 'summary': 'x', 'link': 'http://same', 'published': ''}
    unique_entry = {'title': 'Other headline', 'summary': 'y', 'link': 'http://other', 'published': ''}

    with patch('news_fetcher.fetch_feed', side_effect=[[shared_entry], [shared_entry, unique_entry]]):
        articles = fetch_articles(feeds=DEFAULT_RSS_FEEDS[:2])

    assert articles == [shared_entry, unique_entry]


def test_fetch_articles_without_dedupe_keeps_duplicates():
    shared_entry = {'title': 'Same headline', 'summary': 'x', 'link': 'http://same', 'published': ''}

    with patch('news_fetcher.fetch_feed', side_effect=[[shared_entry], [shared_entry]]):
        articles = fetch_articles(feeds=DEFAULT_RSS_FEEDS[:2], dedupe=False)

    assert articles == [shared_entry, shared_entry]
