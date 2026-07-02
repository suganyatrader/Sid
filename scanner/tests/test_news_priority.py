import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from news_priority import prioritize_stocks


def test_prioritize_stocks_separates_buy_and_short_lists():
    stock_identifiers = [
        {"stock_id": "RELIANCE", "symbol": "RELIANCE", "sector": "Energy", "liquidity_score": 0.95},
        {"stock_id": "TCS", "symbol": "TCS", "sector": "IT", "liquidity_score": 0.88},
        {"stock_id": "INFY", "symbol": "INFY", "sector": "IT", "liquidity_score": 0.8},
    ]
    news_payload = {
        "RELIANCE": {"positive": 5, "negative": 1},
        "TCS": {"positive": 1, "negative": 4},
        "INFY": {"positive": 2, "negative": 2},
    }

    result = prioritize_stocks(stock_identifiers, news_payload, top_n=300)

    assert result["buy"][0]["stock_id"] == "RELIANCE"
    assert result["short"][0]["stock_id"] == "TCS"
    assert all(entry["sentiment"] == "buy" for entry in result["buy"])
    assert all(entry["sentiment"] == "short" for entry in result["short"])


def test_prioritize_stocks_respects_top_n_limit():
    stock_identifiers = [{"stock_id": f"STOCK{i}", "symbol": f"STOCK{i}"} for i in range(1, 6)]
    news_payload = {
        f"STOCK{i}": {"positive": i, "negative": 1}
        for i in range(1, 6)
    }

    result = prioritize_stocks(stock_identifiers, news_payload, top_n=3)

    assert len(result["buy"]) == 3
    assert [entry["stock_id"] for entry in result["buy"]] == ["STOCK5", "STOCK4", "STOCK3"]
