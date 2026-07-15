import argparse
import csv
import datetime
import json
import os
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Optional

from groww_config import GrowwConfig
from rate_limiter import MultiRateLimiter, DEFAULT_GROWW_QUOTE_RATE_LIMITS

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
STOCK_IDENTIFIERS_FILE = DATA_DIR / 'stock_identifiers.json'
TRADE_LOG_FILE = DATA_DIR / 'trade_logs.csv'
DEFAULT_INDEX_HEAVY_SYMBOLS = {
    'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'HUL', 'SBI', 'ITC', 'LT', 'BHARTIARTL',
    'AXISBANK', 'KOTAKBANK', 'MARUTI', 'SUNPHARMA', 'WIPRO', 'ASIANPAINT', 'NTPC', 'TITAN',
    'NESTLEIND', 'M&M', 'POWERGRID', 'ONGC', 'ULTRACEMCO', 'BAJAJ-AUTO', 'JSWSTEEL', 'TATASTEEL',
    'HCLTECH', 'INDUSINDBK', 'ADANIENT', 'ADANIPORTS', 'COALINDIA', 'DRREDDY', 'BPCL', 'HEROMOTOCO',
    'GRASIM', 'EICHERMOT', 'DIVISLAB', 'CIPLA', 'SBILIFE', 'BRITANNIA', 'UPL', 'IOC', 'PIDILITIND',
    'DABUR', 'PNB', 'MUTHOOTFIN', 'GODREJCP',
}


def _to_number(value: Any) -> Optional[float]:
    if value in (None, '', 'None'):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_stock_identifiers(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    target = path or STOCK_IDENTIFIERS_FILE
    if not target.exists():
        return {}
    with target.open('r', encoding='utf-8') as handle:
        payload = json.load(handle)

    if isinstance(payload, dict):
        return {str(item_id): item for item_id, item in payload.items()}

    return {str(item.get('stock_id', idx)): item for idx, item in enumerate(payload) if isinstance(item, dict)}


def fetch_live_quotes(stock_ids: Optional[list[str]] = None, max_workers: int = 4) -> Dict[str, Dict[str, Any]]:
    ids = [str(stock_id) for stock_id in (stock_ids or []) if str(stock_id)]
    if not ids:
        return {}

    config = GrowwConfig.from_env()
    access_token = config.access_token
    if not access_token:
        print('[scanner] Groww access token not set in .env')
        return {}

    try:
        from growwapi import GrowwAPI

        groww = GrowwAPI(access_token)
    except Exception as exc:
        print(f'[scanner] Failed to initialize GrowwAPI client: {exc}')
        return {}

    quote_rate_limiter = MultiRateLimiter(DEFAULT_GROWW_QUOTE_RATE_LIMITS)
    quotes: Dict[str, Dict[str, Any]] = {}

    def _fetch_one(stock_id: str) -> tuple[str, Optional[Dict[str, Any]]]:
        try:
            with quote_rate_limiter:
                quote = groww.get_quote(
                    exchange=groww.EXCHANGE_NSE,
                    segment=groww.SEGMENT_CASH,
                    trading_symbol=stock_id,
                )
            print(f'[scanner] Fetched {stock_id} from Groww SDK')
            return stock_id, {'fetchedAt': datetime.datetime.now().isoformat(), 'quote': quote}
        except Exception as exc:
            print(f'[scanner] Failed to fetch {stock_id}: {exc}')
            return stock_id, None

    worker_count = max(1, min(max_workers, len(ids), 16))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(_fetch_one, stock_id) for stock_id in ids]
        for future in as_completed(futures):
            stock_id, payload = future.result()
            if payload is not None:
                quotes[stock_id] = payload

    return quotes


def _extract_quote_value(quote: Optional[Dict[str, Any]], *candidate_keys: str) -> Optional[Any]:
    if not isinstance(quote, dict):
        return None

    for key in candidate_keys:
        if not key:
            continue
        value = quote.get(key)
        if value in (None, '', 'None'):
            continue
        return value

    for nested_key in ('last_quote', 'quote', 'ohlc', 'market'):
        nested_value = quote.get(nested_key)
        if isinstance(nested_value, dict):
            nested_result = _extract_quote_value(nested_value, *candidate_keys)
            if nested_result not in (None, '', 'None'):
                return nested_result

    return None


def detect_momentum(stock_id: str, quote: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    price = _to_number(
        _extract_quote_value(quote, 'ltp', 'last_price', 'price', 'close')
    )
    if price is None:
        return None

    previous_close = _to_number(
        _extract_quote_value(quote, 'previous_close', 'pc', 'prev_close', 'close')
    ) or 0.0
    volume = _to_number(
        _extract_quote_value(quote, 'volume', 'total_traded_volume', 'total_volume')
    ) or 0.0
    vwap = _to_number(
        _extract_quote_value(quote, 'vwap', 'vw', 'average_price')
    )
    previous_volume = _to_number(
        _extract_quote_value(quote, 'previous_volume', 'avg_volume')
    ) or 0.0

    signals = []
    if previous_close and price > previous_close * 1.01:
        signals.append('breakout')
    if vwap and price > vwap:
        signals.append('bullish_vwap')
    if previous_volume and volume and volume > previous_volume * 1.5:
        signals.append('volume_spike')
    if previous_close and price > previous_close and volume > 0:
        signals.append('momentum_volume')

    momentum_score = len(signals)
    volume_ratio = volume / previous_volume if previous_volume else 0.0

    return {
        'stock_id': stock_id,
        'price': price,
        'previous_close': previous_close,
        'vwap': vwap,
        'volume': volume,
        'volume_ratio': volume_ratio,
        'signals': signals,
        'momentum_score': momentum_score,
    }


def rank_signal(momentum_score: int, volume_ratio: float, liquidity_score: float, index_relevance: float) -> float:
    volume_component = min(max(volume_ratio - 1.0, 0.0), 4.0) * 3.5
    liquidity_component = liquidity_score * 20.0
    index_component = index_relevance * 10.0
    return (momentum_score * 8.0) + volume_component + liquidity_component + index_component


def infer_index_relevance(symbol: str, sector: Optional[str] = None) -> float:
    symbol_upper = (symbol or '').upper()
    if symbol_upper in DEFAULT_INDEX_HEAVY_SYMBOLS:
        return 1.0
    if sector in {'Energy', 'IT', 'Financials', 'Banking', 'Auto', 'FMCG'}:
        return 0.6
    return 0.2


def append_trade_log(stock_id: str, action: str, price: float, volume: int, pnl: float, path: Optional[Path] = None) -> None:
    target = path or TRADE_LOG_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    header = ['trade_id', 'stock_id', 'action', 'price', 'volume', 'timestamp', 'pnl']
    next_id = 1
    had_valid_header = False
    existing_rows: list[list[str]] = []

    if target.exists():
        with target.open('r', encoding='utf-8', newline='') as csvfile:
            existing_rows = list(csv.reader(csvfile))

        if existing_rows:
            first_row = existing_rows[0]
            if first_row == header:
                had_valid_header = True
                for row in existing_rows[1:]:
                    if row and row[0].strip().isdigit():
                        next_id = max(next_id, int(row[0].strip()) + 1)
            else:
                # Recover from older/manual files without headers by inferring IDs
                # from the first column when possible.
                for row in existing_rows:
                    if row and row[0].strip().isdigit():
                        next_id = max(next_id, int(row[0].strip()) + 1)

    write_mode = 'a'
    if target.exists() and existing_rows and not had_valid_header:
        write_mode = 'w'

    with target.open(write_mode, encoding='utf-8', newline='') as csvfile:
        writer = csv.writer(csvfile)
        if write_mode == 'w':
            writer.writerow(header)
            for row in existing_rows:
                if row and row[0].strip().isdigit():
                    writer.writerow(row)
        elif not target.exists() or target.stat().st_size == 0:
            writer.writerow(header)

        writer.writerow([next_id, stock_id, action, price, volume, datetime.datetime.now().isoformat(), pnl])


def run_scanner(
    stock_identifiers_path: Optional[Path] = None,
    trade_log_path: Optional[Path] = None,
    simulate: bool = True,
    live_quotes: Optional[Dict[str, Dict[str, Any]]] = None,
    max_workers: int = 4,
    top_n: int = 10,
    sample_size: Optional[int] = None,
) -> int:
    stock_identifiers = load_stock_identifiers(stock_identifiers_path)
    if not stock_identifiers:
        print('[scanner] No stock identifiers available. Run the updater first.')
        return 0

    stock_ids = list(stock_identifiers.keys())
    if sample_size is not None:
        if sample_size <= 0:
            print('[scanner] sample size must be a positive integer.')
            return 0
        if sample_size < len(stock_ids):
            stock_ids = random.sample(stock_ids, sample_size)
            print(f'[scanner] Selected random sample of {len(stock_ids)} stock identifiers')
        else:
            print(f'[scanner] sample size {sample_size} exceeds available identifiers ({len(stock_ids)}); using all stocks')

    if live_quotes is None:
        live_quotes = fetch_live_quotes(stock_ids, max_workers=max_workers)

    if not live_quotes:
        print('[scanner] No live quotes available. Check credentials or network access.')
        return 0

    ranked_results = []
    for stock_id, payload in live_quotes.items():
        metadata = stock_identifiers.get(stock_id, {})
        quote = payload.get('quote') if isinstance(payload, dict) and isinstance(payload.get('quote'), dict) else payload
        if not isinstance(quote, dict):
            quote = metadata.get('last_quote') or {}

        if not isinstance(quote, dict):
            continue

        result = detect_momentum(stock_id, quote)
        if not result:
            continue

        name = metadata.get('symbol', stock_id)
        signals = result['signals']
        if not signals:
            continue

        liquidity_score = _to_number(metadata.get('liquidity_score')) or 0.0
        index_relevance = infer_index_relevance(name, metadata.get('sector'))
        priority_score = rank_signal(
            momentum_score=result['momentum_score'],
            volume_ratio=result['volume_ratio'],
            liquidity_score=liquidity_score,
            index_relevance=index_relevance,
        )

        ranked_results.append({
            'stock_id': stock_id,
            'name': name,
            'signals': signals,
            'priority_score': priority_score,
            'result': result,
            'metadata': metadata,
        })

    ranked_results.sort(key=lambda entry: entry['priority_score'], reverse=True)

    if not ranked_results:
        print('[scanner] No momentum candidates matched the current quotes.')
        return 0

    for rank, entry in enumerate(ranked_results[:max(1, top_n)], start=1):
        result = entry['result']
        signals = entry['signals']
        name = entry['name']
        print(
            f"[scanner] #{rank} {entry['stock_id']} ({name}) - score={entry['priority_score']:.2f} | "
            f"signals: {', '.join(signals)} | price={result['price']} vwap={result['vwap']} volume={result['volume']}"
        )

        action = 'BUY' if 'breakout' in signals or 'bullish_vwap' in signals else 'HOLD'
        if simulate and action == 'BUY':
            append_trade_log(entry['stock_id'], action, result['price'], 1, 0.0, trade_log_path)
            print(f'[scanner] Simulated BUY for {entry["stock_id"]} at {result["price"]}')

    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Scan live quotes for intraday momentum opportunities')
    parser.add_argument('--stock-identifiers-file', type=Path, default=STOCK_IDENTIFIERS_FILE)
    parser.add_argument('--trade-log-file', type=Path, default=TRADE_LOG_FILE)
    parser.add_argument('--no-simulate', action='store_true', help='Analyze signals without writing trade logs')
    parser.add_argument('--max-workers', type=int, default=int(os.getenv('SCAN_WORKERS', '4')), help='Concurrent workers for quote fetching')
    parser.add_argument('--top-n', type=int, default=10, help='Number of ranked candidates to display')
    parser.add_argument('--sample-size', type=int, help='Pick a random subset of stock identifiers before fetching quotes')
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run_scanner(
        stock_identifiers_path=args.stock_identifiers_file,
        trade_log_path=args.trade_log_file,
        simulate=not args.no_simulate,
        max_workers=args.max_workers,
        top_n=args.top_n,
        sample_size=args.sample_size,
    )


if __name__ == '__main__':
    raise SystemExit(main())