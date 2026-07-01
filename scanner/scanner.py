import argparse
import csv
import datetime
import json
import os
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
    try:
        access_token = config.get_access_token()
    except ValueError as exc:
        print(f'[scanner] {exc}')
        return {}
    except Exception as exc:
        print(f'[scanner] Failed to obtain Groww access token: {exc}')
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


def detect_momentum(stock_id: str, quote: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    price = _to_number(
        quote.get('ltp') or quote.get('last_price') or quote.get('price') or quote.get('close')
    )
    if price is None:
        return None

    previous_close = _to_number(
        quote.get('previous_close') or quote.get('pc') or quote.get('close') or 0
    ) or 0.0
    volume = _to_number(
        quote.get('volume') or quote.get('total_traded_volume') or quote.get('total_volume') or 0
    ) or 0.0
    vwap = _to_number(
        quote.get('vwap') or quote.get('vw') or quote.get('average_price')
    )
    previous_volume = _to_number(
        quote.get('previous_volume') or quote.get('avg_volume') or 0
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

    if target.exists():
        with target.open('r', encoding='utf-8', newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)
            if rows:
                next_id = int(rows[-1]['trade_id']) + 1

    with target.open('a', encoding='utf-8', newline='') as csvfile:
        writer = csv.writer(csvfile)
        if next_id == 1 and not target.exists():
            writer.writerow(header)
        elif next_id == 1:
            with target.open('r', encoding='utf-8', newline='') as existing:
                if not existing.read(1):
                    writer.writerow(header)
        writer.writerow([next_id, stock_id, action, price, volume, datetime.datetime.now().isoformat(), pnl])


def run_scanner(
    stock_identifiers_path: Optional[Path] = None,
    trade_log_path: Optional[Path] = None,
    simulate: bool = True,
    live_quotes: Optional[Dict[str, Dict[str, Any]]] = None,
    max_workers: int = 4,
    top_n: int = 10,
) -> int:
    stock_identifiers = load_stock_identifiers(stock_identifiers_path)
    if not stock_identifiers:
        print('[scanner] No stock identifiers available. Run the updater first.')
        return 0

    if live_quotes is None:
        live_quotes = fetch_live_quotes(list(stock_identifiers.keys()), max_workers=max_workers)

    if not live_quotes:
        print('[scanner] No live quotes available. Check credentials or network access.')
        return 0

    ranked_results = []
    for stock_id, payload in live_quotes.items():
        quote = payload.get('quote') if isinstance(payload, dict) and isinstance(payload.get('quote'), dict) else payload
        result = detect_momentum(stock_id, quote)
        if not result:
            continue

        metadata = stock_identifiers.get(stock_id, {})
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
    )


if __name__ == '__main__':
    raise SystemExit(main())