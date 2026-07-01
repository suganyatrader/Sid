import json
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from scanner import detect_momentum, rank_signal


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
