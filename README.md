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

3. Configure Groww credentials in your environment:

```bash
set GROWW_API_KEY=your_api_key
set GROWW_API_SECRET=your_api_secret
```

If you already have an access token, use:

```bash
set GROWW_ACCESS_TOKEN=your_access_token
```

4. Run the scanner:

```bash
python scanner.py
```

> Note: The Node.js ingestion layer is now optional and is retained only as a legacy alternate collector.
## Data files

- `data/stock_identifiers.json`: stock identifiers used by both layers.
- `data/live_quotes.json`: latest quote captures from ingestion.
- `data/trade_logs.csv`: virtual trade history and P&L.

## Notes

- This first version uses a file-based persistence model for simplicity.
- The ingestion layer can run in real API mode or fallback to simulated quotes when API credentials are not available.
