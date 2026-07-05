import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from news_priority import build_parser, load_news_payload, prioritize_stocks, write_priority_lists


def test_build_parser_defaults_news_data_to_none():
    args = build_parser().parse_args([])

    assert args.news_data is None


def test_load_news_payload_returns_empty_when_no_path_or_payload():
    assert load_news_payload() == {}


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
    assert result["buy"][0]["sentiment"] == "buy"
    assert result["short"][0]["sentiment"] == "short"


def test_prioritize_stocks_respects_top_n_limit():
    stock_identifiers = [{"stock_id": f"STOCK{i}", "symbol": f"STOCK{i}"} for i in range(1, 6)]
    news_payload = {
        f"STOCK{i}": {"positive": i, "negative": 1}
        for i in range(1, 6)
    }

    result = prioritize_stocks(stock_identifiers, news_payload, top_n=3)

    assert len(result["buy"]) == 3
    assert [entry["stock_id"] for entry in result["buy"]] == ["STOCK5", "STOCK4", "STOCK3"]


def test_load_news_payload_accepts_inline_json_string():
    payload = load_news_payload(payload='{"RELIANCE": {"positive": 4, "negative": 1}}')

    assert payload["RELIANCE"]["positive"] == 4
    assert payload["RELIANCE"]["negative"] == 1


def test_prioritize_stocks_includes_all_identifiers_even_without_news():
    stock_identifiers = [
        {"stock_id": "A", "symbol": "A"},
        {"stock_id": "B", "symbol": "B"},
        {"stock_id": "C", "symbol": "C"},
    ]
    news_payload = {
        "A": {"positive": 2, "negative": 1},
        "B": {"positive": 0, "negative": 2},
    }

    result = prioritize_stocks(stock_identifiers, news_payload, top_n=10)

    ranked_ids = {entry["stock_id"] for entry in result["buy"] + result["short"]}

    assert ranked_ids == {"A", "B", "C"}


def test_write_priority_lists_includes_generated_timestamp(tmp_path):
    output_path = tmp_path / "output.json"
    write_priority_lists({"buy": [], "short": []}, output_path)

    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert "generated_at" in payload
    assert payload["buy"] == []
    assert payload["short"] == []
