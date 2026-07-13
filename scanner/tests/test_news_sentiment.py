import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.append(str(Path(__file__).resolve().parents[1]))

from news_sentiment import _chunk, analyze_articles, build_batch_prompt


class _StubConfig:
    def __init__(self, client, model='llama-3.3-70b-versatile'):
        self._client = client
        self.model = model

    def get_client(self):
        return self._client


def _mock_response(content: str):
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    return response


def _articles(n):
    return [{'title': f'Headline {i}', 'summary': f'Summary {i}'} for i in range(n)]


def _stock_identifiers(symbols):
    return [{'stock_id': s, 'symbol': s} for s in symbols]


def test_chunk_splits_into_batches():
    assert _chunk(list(range(7)), 3) == [[0, 1, 2], [3, 4, 5], [6]]


def test_build_batch_prompt_includes_symbols_and_articles():
    system_prompt, user_prompt = build_batch_prompt(['RELIANCE', 'TCS'], _articles(2))

    assert 'RELIANCE' in system_prompt
    assert 'TCS' in system_prompt
    assert 'Headline 0' in user_prompt
    assert 'Headline 1' in user_prompt


def test_analyze_articles_aggregates_counts_across_batches():
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _mock_response(json.dumps({'RELIANCE': {'positive': 2, 'negative': 0}})),
        _mock_response(json.dumps({'RELIANCE': {'positive': 1, 'negative': 1}})),
    ]
    config = _StubConfig(client)

    result = analyze_articles(
        _articles(2),
        _stock_identifiers(['RELIANCE', 'TCS']),
        config=config,
        articles_per_batch=1,
    )

    assert result['RELIANCE'] == {'positive': 3, 'negative': 1}


def test_analyze_articles_drops_symbols_outside_roster():
    client = MagicMock()
    client.chat.completions.create.return_value = _mock_response(
        json.dumps({'RELIANCE': {'positive': 1, 'negative': 0}, 'FAKESYM': {'positive': 5, 'negative': 5}})
    )
    config = _StubConfig(client)

    result = analyze_articles(
        _articles(1),
        _stock_identifiers(['RELIANCE']),
        config=config,
        articles_per_batch=25,
    )

    assert result == {'RELIANCE': {'positive': 1, 'negative': 0}}


def test_analyze_articles_skips_failed_batch_without_affecting_others():
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        Exception('groq error'),
        _mock_response(json.dumps({'TCS': {'positive': 1, 'negative': 0}})),
    ]
    config = _StubConfig(client)

    result = analyze_articles(
        _articles(2),
        _stock_identifiers(['RELIANCE', 'TCS']),
        config=config,
        articles_per_batch=1,
    )

    assert result == {'TCS': {'positive': 1, 'negative': 0}}


def test_analyze_articles_respects_batch_size():
    client = MagicMock()
    client.chat.completions.create.return_value = _mock_response(json.dumps({}))
    config = _StubConfig(client)

    analyze_articles(
        _articles(5),
        _stock_identifiers(['RELIANCE']),
        config=config,
        articles_per_batch=2,
    )

    assert client.chat.completions.create.call_count == 3


def test_analyze_articles_returns_empty_for_no_articles():
    config = _StubConfig(MagicMock())

    result = analyze_articles([], _stock_identifiers(['RELIANCE']), config=config)

    assert result == {}
