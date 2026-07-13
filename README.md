# Intraday Momentum Trading Utility

A single repo for intraday momentum scanning with a Python-first Groww SDK flow.

- `scanner/` — Python momentum scanner, Groww SDK-based live quote collector, virtual trading engine, and CLI.
- `data/` — File-based persistence using JSON and CSV.

## Architecture

- `scanner/`: Fetches live stock quotes with `growwapi`, detects momentum signals, and simulates trades into `data/trade_logs.csv`.
- `data/`: File-based persistence using JSON and CSV.

## Setup

### Python scanner

1. Navigate to `scanner/`
2. Install dependencies:

```bash
cd scanner
python -m pip install -r requirements.txt
```

3. Configure Groww credentials in your environment, or use a `.env` file in `scanner/`:

```bash
set GROWW_API_KEY=your_api_key
set GROWW_API_SECRET=your_api_secret
```

Or create `scanner/.env` with:

```ini
GROWW_API_KEY=your_api_key
GROWW_API_SECRET=your_api_secret
```

The scanner now automatically loads `scanner/.env` even when run from the repository root.

If you already have an access token, use:

```bash
set GROWW_ACCESS_TOKEN=your_access_token
```

Or `scanner/.env`:

```ini
GROWW_ACCESS_TOKEN=your_access_token
```
If you have a valid access token, configure it in `scanner/.env` or the environment:

```ini
GROWW_ACCESS_TOKEN=your_access_token
```

This is the simplest option and is used immediately by the scanner.

If you prefer TOTP auth instead, one of these is also supported:

```ini
GROWW_API_KEY=your_api_key
GROWW_TOTP_SECRET=your_totp_secret
```

When `GROWW_TOTP_SECRET` is present, the scanner generates a fresh one-time code automatically.

If you cannot use a secret, you may supply a current TOTP value directly:

```ini
GROWW_API_KEY=your_api_key
GROWW_TOTP_TOKEN=123456
```
> Note: The current scanner uses `data/stock_identifiers.json` for stock symbols. `STOCK_IDS` and `GROWW_BASE_URL` in `.env` are not used by the current scanner flow.4. Run the scanner:

```bash
python scanner.py
```

## Data files

- `data/stock_identifiers.json`: stock identifiers used by the scanner.
- `data/trade_logs.csv`: virtual trade history and P&L.

## Notes

- This first version uses a file-based persistence model for simplicity.

## News-based stock prioritization

Use the separate news prioritizer to rank stocks from the previous day’s news sentiment into two lists:

- `buy`: stocks with more positive than negative mentions
- `short`: stocks with more negative than positive mentions

Run it from the repository root:

```bash
python scanner/news_priority.py --top-n 300
```

The script reads from `data/stock_identifiers.json` and `data/previous_day_news.json`, then writes the ranked output to `data/news_priority_lists.json`.
