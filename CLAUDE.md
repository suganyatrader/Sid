# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands run from the `scanner/` directory (or repo root, per the note below).

```bash
cd scanner
python -m pip install -r requirements.txt   # install deps (growwapi, pyotp, python-dotenv, pandas, requests, feedparser, groq, openai)

python -m pytest tests/                     # run all tests
python -m pytest tests/test_news_priority.py -k test_prioritize_stocks_respects_top_n_limit  # run a single test

python scanner.py                           # run the momentum scanner (fetches live quotes, simulates trades)
python scanner.py --no-simulate --sample-size 20 --top-n 5   # dry run against a random subset

python news_priority.py --top-n 100         # fetch live Moneycontrol news, analyze via LLM (Groq or Ollama), rank buy/short lists (top 100 each)
python news_priority.py --news-data data/manual_news.json --top-n 100  # offline override: skip live fetch/LLM, use a JSON payload instead
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

The live news-sentiment pipeline in `news_priority.py` (`news_fetcher.py` + `news_sentiment.py`) uses `LlmConfig.from_env()` (`scanner/llm_config.py`) — a self-contained duplicate of the same `.env`-loading pattern used by `GrowwConfig`, not shared with it. Provider is selected by the `LLM_PROVIDER` env var (defaults to `groq`), or the `USE_OLLAMA` shorthand flag:

**Groq provider:**
1. `GROQ_API_KEY` — required.
2. `GROQ_MODEL` — optional, defaults to `llama-3.3-70b-versatile`.

**Ollama provider** (set `LLM_PROVIDER=ollama` or `USE_OLLAMA=true`):
1. `OLLAMA_MODEL` — optional, defaults to `qwen3.6`.
2. `OLLAMA_BASE_URL` — optional, defaults to `http://127.0.0.1:11434`.

This pipeline does not talk to Groww / need Groww credentials.

## Architecture

This is a file-based (no DB, no server) intraday trading utility for NSE stocks, built around three independent CLI scripts in `scanner/` that share on-disk JSON/CSV as their integration point:

- **`update_stock_identifier.py`** — populates/refreshes `data/stock_identifiers.json`, the master list of tradable stocks. `--discover` pulls NSE cash instruments flagged `is_intraday` from `GrowwAPI.get_all_instruments()` (falling back to a hardcoded `DEFAULT_INTRADAY_SYMBOLS` list of ~45 index-heavy names if the API call fails). Per-symbol updates fetch a live quote to confirm the symbol resolves and cache it as `last_quote`.

- **`scanner.py`** — the live momentum scanner. Reads `data/stock_identifiers.json`, fetches live quotes concurrently via a `ThreadPoolExecutor` (rate-limited — see below), scores each stock with `detect_momentum()` (breakout / bullish_vwap / volume_spike / momentum_volume signals) and `rank_signal()` (weights momentum, volume ratio, liquidity_score, and index relevance), then prints the ranked list and simulates BUY trades by appending to `data/trade_logs.csv`. Quote field lookups (`ltp`, `previous_close`, `vwap`, etc.) go through `_extract_quote_value()`, which also recurses into nested `last_quote`/`quote`/`ohlc`/`market` dicts — the Groww SDK's response shape is not fully consistent, so this normalization layer matters.

- **`news_priority.py`** — a separate, independent ranking pipeline. Reads `data/stock_identifiers.json` and, by default, fetches live news via `news_fetcher.fetch_articles()` and analyzes it via `news_sentiment.analyze_articles()` (see below) to get per-symbol positive/negative mention counts, then scores stocks by news sentiment margin + liquidity + index relevance into `buy`/`short`/`neutral` buckets, writing `data/news_priority_lists.json` with a `generated_at` timestamp. Both buy and short lists backfill with neutral entries if they don't reach `top_n`. Passing `--news-data <path-or-json>` skips the live fetch/LLM call entirely and uses that payload instead (useful for offline runs/tests). This pipeline does not talk to Groww at all, but does require Groq credentials for its default (live) mode — see Credentials above.

- **`news_fetcher.py`** — fetches and parses Moneycontrol RSS feeds (`DEFAULT_RSS_FEEDS`: latest news, market reports, buzzing stocks, results, economy). Pure I/O, no LLM involved; per-feed failures are caught and logged, returning `[]` for that feed rather than raising. `fetch_articles()` concatenates all feeds and dedupes by `(title, link)`.

- **`news_sentiment.py`** — turns a list of fetched articles into the same `{symbol: {"positive": n, "negative": n}}` shape `prioritize_stocks()` expects. Chunks articles into batches (`ARTICLES_PER_BATCH = 3`) and sends each batch, alongside the bare list of valid ticker symbols from `stock_identifiers.json`, to an LLM (Groq or Ollama) in parallel (`ThreadPoolExecutor`, rate-limited via `MultiRateLimiter(DEFAULT_GROQ_RATE_LIMITS)`) — this bulk-fetch-then-map design (few large LLM calls against batches of articles, rather than one call per stock) keeps token usage and API calls low relative to querying each of the 500+ symbols individually. The LLM's response is treated as untrusted: any ticker symbol it returns that isn't in the real roster is dropped before aggregation. One batch failing (bad JSON, API error) is logged and skipped without affecting other batches.

- **`rate_limiter.py`** — `RateLimiter` (single sliding-window limiter) and `MultiRateLimiter` (layers several limiters, e.g. 10 calls/sec AND 300 calls/min — see `DEFAULT_GROWW_QUOTE_RATE_LIMITS`, or 25 calls/min for Groq — see `DEFAULT_GROQ_RATE_LIMITS`). Used as a context manager around every `groww.get_quote()` call in `scanner.py`/`update_stock_identifier.py`, and around every Groq batch call in `news_sentiment.py`, to stay under each API's rate limits during concurrent fetches.

Both `scanner.py` and `news_priority.py` independently define `DEFAULT_INDEX_HEAVY_SYMBOLS`/`_infer_index_relevance` — an index-membership heuristic (hardcoded set of large-cap symbols, else scored by sector) used to boost priority scores for index-heavy stocks. These are duplicated, not shared, across the two files.

`data/` holds all persisted state: `stock_identifiers.json` (master symbol list + cached quotes) and `news_priority_lists.json` (output of `news_priority.py`), plus `trade_logs.csv` (append-only simulated trade history, auto-incrementing `trade_id`).
