# Intraday Momentum Trading Utility

A hybrid monorepo for intraday momentum scanning with:

- `ingestion/` — Node.js Groww data ingestion and quote collector.
- `scanner/` — Python momentum scanner, virtual trading engine, and CLI.
- `data/` — File-based persistence using JSON and CSV.

## Architecture

- `ingestion/`: Fetches live stock quotes from Groww API and writes them to `data/live_quotes.json`.
- `scanner/`: Loads shared data, detects momentum signals, and simulates trades into `data/trade_logs.csv`.

## Setup

### Node.js ingestion

1. Navigate to `ingestion/`
2. Create a `.env` file from `.env.example`
3. Install dependencies:

```bash
cd ingestion
npm install
```

4. Run ingestion:

```bash
npm run ingest
```

### Python scanner

1. Navigate to `scanner/`
2. Install dependencies:

```bash
cd scanner
python -m pip install -r requirements.txt
```

3. Run the scanner:

```bash
python scanner.py --run
```

## Data files

- `data/stock_identifiers.json`: stock identifiers used by both layers.
- `data/live_quotes.json`: latest quote captures from ingestion.
- `data/trade_logs.csv`: virtual trade history and P&L.

## Notes

- This first version uses a file-based persistence model for simplicity.
- The ingestion layer can run in real API mode or fallback to simulated quotes when API credentials are not available.
