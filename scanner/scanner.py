import argparse
import csv
import datetime
import json
from pathlib import Path
from typing import Any, Dict, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
STOCK_IDENTIFIERS_FILE = DATA_DIR / 'stock_identifiers.json'
LIVE_QUOTES_FILE = DATA_DIR / 'live_quotes.json'
TRADE_LOG_FILE = DATA_DIR / 'trade_logs.csv'


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

    return {str(item['stock_id']): item for item in payload if isinstance(item, dict)}


def load_live_quotes(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    target = path or LIVE_QUOTES_FILE
    if not target.exists():
        return {}
    with target.open('r', encoding='utf-8') as handle:
        payload = json.load(handle)

    if isinstance(payload, dict):
        return {str(stock_id): quote for stock_id, quote in payload.items()}

    if isinstance(payload, list):
        return {str(item.get('stock_id') or item.get('symbol') or idx): item for idx, item in enumerate(payload)}

    return {}


def detect_momentum(stock_id: str, quote: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    price = _to_number(quote.get('ltp'))
    if price is None:
        return None

    previous_close = _to_number(quote.get('previous_close') or quote.get('pc') or 0) or 0.0
    volume = _to_number(quote.get('volume') or quote.get('total_traded_volume') or 0) or 0.0
    vwap = _to_number(quote.get('vwap') or quote.get('vw'))
    previous_volume = _to_number(quote.get('previous_volume') or quote.get('avg_volume') or 0) or 0.0

    signals = []
    if previous_close and price > previous_close * 1.01:
        signals.append('breakout')
    if vwap and price > vwap:
        signals.append('bullish_vwap')
    if previous_volume and volume and volume > previous_volume * 1.5:
        signals.append('volume_spike')
    if previous_close and price > previous_close and volume > 0:
        signals.append('momentum_volume')

    return {
        'stock_id': stock_id,
        'price': price,
        'previous_close': previous_close,
        'vwap': vwap,
        'volume': volume,
        'signals': signals,
    }


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
    live_quotes_path: Optional[Path] = None,
    trade_log_path: Optional[Path] = None,
    simulate: bool = True,
) -> int:
    stock_identifiers = load_stock_identifiers(stock_identifiers_path)
    live_quotes = load_live_quotes(live_quotes_path)

    if not live_quotes:
        print('[scanner] No live quotes available. Run ingestion first.')
        return 0

    for stock_id, payload in live_quotes.items():
        quote = payload.get('quote') if isinstance(payload, dict) and isinstance(payload.get('quote'), dict) else payload
        result = detect_momentum(stock_id, quote)
        if not result:
            continue

        name = stock_identifiers.get(stock_id, {}).get('symbol', stock_id)
        signals = result['signals']
        if not signals:
            print(f'[scanner] {stock_id} ({name}) - no momentum signal')
            continue

        print(
            f"[scanner] {stock_id} ({name}) - signals: {', '.join(signals)} | "
            f"price={result['price']} vwap={result['vwap']} volume={result['volume']}"
        )

        action = 'BUY' if 'breakout' in signals or 'bullish_vwap' in signals else 'HOLD'
        if simulate and action == 'BUY':
            volume = 1
            pnl = 0.0
            append_trade_log(stock_id, action, result['price'], volume, pnl, trade_log_path)
            print(f'[scanner] Simulated BUY for {stock_id} at {result["price"]}')

    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Scan live quotes for intraday momentum opportunities')
    parser.add_argument('--stock-identifiers-file', type=Path, default=STOCK_IDENTIFIERS_FILE)
    parser.add_argument('--live-quotes-file', type=Path, default=LIVE_QUOTES_FILE)
    parser.add_argument('--trade-log-file', type=Path, default=TRADE_LOG_FILE)
    parser.add_argument('--no-simulate', action='store_true', help='Analyze signals without writing trade logs')
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run_scanner(
        stock_identifiers_path=args.stock_identifiers_file,
        live_quotes_path=args.live_quotes_file,
        trade_log_path=args.trade_log_file,
        simulate=not args.no_simulate,
    )


if __name__ == '__main__':
    raise SystemExit(main())