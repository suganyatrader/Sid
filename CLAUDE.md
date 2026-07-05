# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands run from the `scanner/` directory (or repo root, per the note below).

```bash
cd scanner
python -m pip install -r requirements.txt   # install deps (growwapi, pyotp, python-dotenv, pandas)

python -m pytest tests/                     # run all tests
python -m pytest tests/test_news_priority.py -k test_prioritize_stocks_respects_top_n_limit  # run a single test

python scanner.py                           # run the momentum scanner (fetches live quotes, simulates trades)
python scanner.py --no-simulate --sample-size 20 --top-n 5   # dry run against a random subset

python news_priority.py --top-n 300         # rank stocks into buy/short lists from news sentiment
python update_stock_identifier.py --discover              # populate data/stock_identifiers.json from Groww's instrument catalog
python update_stock_identifier.py --stock-id RELIANCE      # refresh a single identifier's Groww symbol/quote
```

There is no lint/format tooling configured in this repo.

Tests append `scanner/` to `sys.path` manually (see `scanner/tests/*.py`), so there's no package install step needed for imports to resolve.

## Credentials

The Groww SDK requires auth, resolved in this priority order by `GrowwConfig.from_env()` (`scanner/groww_config.py`):
1. `GROWW_ACCESS_TOKEN` — used directly.
2. `GROWW_API_KEY` + `GROWW_API_SECRET`.
3. `GROWW_API_KEY` + `GROWW_TOTP_SECRET` (a fresh TOTP is generated via `pyotp` on each call) or `GROWW_TOTP_TOKEN` (a literal numeric OTP).

Config is loaded from `scanner/.env` (via `python-dotenv`, falling back to a hand-rolled parser if it's not installed) even when scripts are invoked from the repo root. `STOCK_IDS` and `GROWW_BASE_URL` env vars are not used by the current flow.

## Architecture

This is a file-based (no DB, no server) intraday trading utility for NSE stocks, built around three independent CLI scripts in `scanner/` that share on-disk JSON/CSV as their integration point:

- **`update_stock_identifier.py`** — populates/refreshes `data/stock_identifiers.json`, the master list of tradable stocks. `--discover` pulls NSE cash instruments flagged `is_intraday` from `GrowwAPI.get_all_instruments()` (falling back to a hardcoded `DEFAULT_INTRADAY_SYMBOLS` list of ~45 index-heavy names if the API call fails). Per-symbol updates fetch a live quote to confirm the symbol resolves and cache it as `last_quote`.

- **`scanner.py`** — the live momentum scanner. Reads `data/stock_identifiers.json`, fetches live quotes concurrently via a `ThreadPoolExecutor` (rate-limited — see below), scores each stock with `detect_momentum()` (breakout / bullish_vwap / volume_spike / momentum_volume signals) and `rank_signal()` (weights momentum, volume ratio, liquidity_score, and index relevance), then prints the ranked list and simulates BUY trades by appending to `data/trade_logs.csv`. Quote field lookups (`ltp`, `previous_close`, `vwap`, etc.) go through `_extract_quote_value()`, which also recurses into nested `last_quote`/`quote`/`ohlc`/`market` dicts — the Groww SDK's response shape is not fully consistent, so this normalization layer matters.

- **`news_priority.py`** — a separate, independent ranking pipeline. Reads the same `data/stock_identifiers.json` plus `data/previous_day_news.json` (per-symbol positive/negative mention counts) and scores stocks by news sentiment margin + liquidity + index relevance into `buy`/`short`/`neutral` buckets, writing `data/news_priority_lists.json` with a `generated_at` timestamp. Both buy and short lists backfill with neutral entries if they don't reach `top_n`. This pipeline does not talk to Groww at all and can run without credentials.

- **`rate_limiter.py`** — `RateLimiter` (single sliding-window limiter) and `MultiRateLimiter` (layers several limiters, e.g. 10 calls/sec AND 300 calls/min — see `DEFAULT_GROWW_QUOTE_RATE_LIMITS`). Used as a context manager around every `groww.get_quote()` call in both `scanner.py` and `update_stock_identifier.py` to stay under Groww's API limits during concurrent fetches.

Both `scanner.py` and `news_priority.py` independently define `DEFAULT_INDEX_HEAVY_SYMBOLS`/`_infer_index_relevance` — an index-membership heuristic (hardcoded set of large-cap symbols, else scored by sector) used to boost priority scores for index-heavy stocks. These are duplicated, not shared, across the two files.

`data/` holds all persisted state: `stock_identifiers.json` (master symbol list + cached quotes), `previous_day_news.json` (news sentiment input), `news_priority_lists.json` (output of `news_priority.py`), and `trade_logs.csv` (append-only simulated trade history, auto-incrementing `trade_id`).
