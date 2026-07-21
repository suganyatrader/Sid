import csv
import json
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import scanner as scanner_module
from scanner import detect_momentum, rank_signal, run_scanner


def test_detect_momentum_returns_signals_for_breakout():
    quote = {
        'ltp': 110.0,
        'previous_close': 100.0,
        'vwap': 105.0,
        'volume': 400000,
        'previous_volume': 100000,
    }

    result = detect_momentum('TEST', quote)

    assert result is not None
    assert 'breakout' in result['signals']
    assert 'bullish_vwap' in result['signals']
    assert 'volume_spike' in result['signals']
    assert 'momentum_volume' in result['signals']


def test_rank_signal_prioritizes_liquidity_and_volume():
    score = rank_signal(
        momentum_score=4,
        volume_ratio=2.0,
        liquidity_score=0.9,
        index_relevance=0.8,
    )

    assert score > 0
    assert score > 4.0


def test_detect_momentum_uses_nested_last_quote_fields():
    quote = {
        'last_price': 110.0,
        'ohlc': {'close': 100.0},
        'volume': 400000,
        'previous_volume': 100000,
    }

    result = detect_momentum('TEST', quote)

    assert result is not None
    assert 'breakout' in result['signals']
    assert 'momentum_volume' in result['signals']


def test_detect_momentum_detects_modest_intraday_upmoves():
    quote = {
        'last_price': 103.5,
        'ohlc': {'close': 100.0},
        'volume': 400000,
    }

    result = detect_momentum('TEST', quote)

    assert result is not None
    assert 'breakout' in result['signals']


def test_run_scanner_fetches_in_300_stock_batches(tmp_path, monkeypatch):
    total_stocks = 650
    stock_identifiers = {
        f'STK{i:04d}': {
            'symbol': f'STK{i:04d}',
            'liquidity_score': 0.5,
        }
        for i in range(total_stocks)
    }

    stock_identifiers_path = tmp_path / 'stock_identifiers.json'
    stock_identifiers_path.write_text(json.dumps(stock_identifiers), encoding='utf-8')

    calls = []

    def fake_fetch(stock_ids=None, max_workers=4):
        calls.append(len(stock_ids or []))
        return {
            stock_id: {
                'quote': {
                    'ltp': 105.0,
                    'previous_close': 100.0,
                    'vwap': 102.0,
                    'volume': 200000,
                    'previous_volume': 100000,
                }
            }
            for stock_id in (stock_ids or [])
        }

    monkeypatch.setattr(scanner_module, 'fetch_live_quotes', fake_fetch)
    monkeypatch.setattr(scanner_module.time, 'sleep', lambda _seconds: None)

    result = run_scanner(
        stock_identifiers_path=stock_identifiers_path,
        trade_log_path=tmp_path / 'trade_logs.csv',
        simulate=False,
        batch_size=300,
        batch_period_seconds=60,
        top_n=5,
    )

    assert result == 1
    assert calls == [300, 300, 50]


def test_run_scanner_writes_only_final_top_20_trades(tmp_path):
    total_stocks = 25
    stock_identifiers = {}
    live_quotes = {}
    scores = {}

    for i in range(total_stocks):
        stock_id = f'STK{i:04d}'
        liquidity = i / 100.0
        volume_ratio = 1.0 + (i / 10.0)
        previous_volume = 100000
        volume = int(previous_volume * volume_ratio)

        stock_identifiers[stock_id] = {
            'symbol': stock_id,
            'liquidity_score': liquidity,
        }
        live_quotes[stock_id] = {
            'quote': {
                'ltp': 105.0,
                'previous_close': 100.0,
                'vwap': 102.0,
                'volume': volume,
                'previous_volume': previous_volume,
            }
        }

        scores[stock_id] = rank_signal(
            momentum_score=4,
            volume_ratio=volume_ratio,
            liquidity_score=liquidity,
            index_relevance=0.2,
        )

    stock_identifiers_path = tmp_path / 'stock_identifiers.json'
    trade_log_path = tmp_path / 'trade_logs.csv'
    stock_identifiers_path.write_text(json.dumps(stock_identifiers), encoding='utf-8')

    result = run_scanner(
        stock_identifiers_path=stock_identifiers_path,
        trade_log_path=trade_log_path,
        simulate=True,
        live_quotes=live_quotes,
        top_n=10,
        trade_log_top_n=20,
    )

    assert result == 1
    assert trade_log_path.exists()

    with trade_log_path.open('r', encoding='utf-8', newline='') as csvfile:
        rows = list(csv.DictReader(csvfile))

    expected_top_20 = {
        stock_id
        for stock_id, _score in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:20]
    }

    assert len(rows) == 20
    assert set(row['stock_id'] for row in rows) == expected_top_20
