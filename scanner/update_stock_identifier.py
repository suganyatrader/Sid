"""
Update stock identifiers with Groww symbol format and discover NSE intraday-ready symbols.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional

from groww_config import GrowwConfig
from rate_limiter import MultiRateLimiter, DEFAULT_GROWW_QUOTE_RATE_LIMITS


DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
STOCK_IDENTIFIERS_FILE = DATA_DIR / 'stock_identifiers.json'
DEFAULT_INTRADAY_SYMBOLS = [
    'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'HUL', 'SBI', 'ITC', 'LT', 'BHARTIARTL',
    'AXISBANK', 'KOTAKBANK', 'MARUTI', 'SUNPHARMA', 'WIPRO', 'ASIANPAINT', 'NTPC', 'TITAN', 'NESTLEIND',
    'M&M', 'POWERGRID', 'ONGC', 'ULTRACEMCO', 'BAJAJ-AUTO', 'JSWSTEEL', 'TATASTEEL', 'HCLTECH',
    'INDUSINDBK', 'ADANIENT', 'ADANIPORTS', 'COALINDIA', 'DRREDDY', 'BPCL', 'HEROMOTOCO', 'GRASIM',
    'EICHERMOT', 'DIVISLAB', 'CIPLA', 'SBILIFE', 'BRITANNIA', 'UPL', 'IOC', 'PIDILITIND', 'DABUR',
    'PNB', 'MUTHOOTFIN', 'GODREJCP',
]


def build_groww_symbol(
    exchange: str,
    trading_symbol: str,
    expiry_date: Optional[str] = None,
    strike_price: Optional[float] = None,
    option_type: Optional[str] = None,
) -> str:
    components = [exchange, trading_symbol]

    if expiry_date:
        components.append(expiry_date)

    if strike_price is not None:
        components.append(str(int(strike_price)))

    if option_type:
        components.append(option_type)

    return '-'.join(components)


quote_rate_limiter = MultiRateLimiter(DEFAULT_GROWW_QUOTE_RATE_LIMITS)

def find_ticker_symbol(
    symbol: str,
    exchange: str = 'NSE',
    access_token: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        config = GrowwConfig.from_env() if not access_token else GrowwConfig(access_token=access_token)
        token = access_token or config.access_token

        if not token:
            raise ValueError('Access token not found')

        from growwapi import GrowwAPI

        groww = GrowwAPI(token)

        exchanges_to_try = [exchange]
        if exchange.upper() == 'NSE':
            exchanges_to_try.append('BSE')
        elif exchange.upper() == 'BSE':
            exchanges_to_try.append('NSE')

        for exch in exchanges_to_try:
            try:
                with quote_rate_limiter:
                    quote = groww.get_quote(
                        exchange=exch,
                        segment=groww.SEGMENT_CASH,
                        trading_symbol=symbol,
                    )

                groww_symbol = build_groww_symbol(exch, symbol)
                return {
                    'trading_symbol': symbol,
                    'exchange': exch,
                    'groww_symbol': groww_symbol,
                    'quote': quote,
                    'found': True,
                }
            except Exception:
                continue

        groww_symbol = build_groww_symbol(exchange, symbol)
        return {
            'trading_symbol': symbol,
            'exchange': exchange,
            'groww_symbol': groww_symbol,
            'found': False,
            'error': 'Symbol not found in API, but Groww symbol constructed',
        }

    except Exception as exc:
        return {
            'trading_symbol': symbol,
            'error': str(exc),
            'found': False,
        }


def load_stock_identifiers(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    target = path or STOCK_IDENTIFIERS_FILE

    if not target.exists():
        print(f'File not found: {target}')
        return {}

    with target.open('r', encoding='utf-8') as handle:
        payload = json.load(handle)

    if isinstance(payload, dict):
        return {str(item_id): item for item_id, item in payload.items()}

    return {str(item.get('stock_id', idx)): item for idx, item in enumerate(payload) if isinstance(item, dict)}


def save_stock_identifiers(identifiers: Dict[str, Dict[str, Any]], path: Optional[Path] = None) -> bool:
    target = path or STOCK_IDENTIFIERS_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = list(identifiers.values()) if identifiers else []

    with target.open('w', encoding='utf-8') as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)

    print(f'Saved {len(identifiers)} stock identifiers to {target}')
    return True


def update_stock_identifier(
    stock_id: str,
    exchange: str = 'NSE',
    access_token: Optional[str] = None,
    stock_identifiers_path: Optional[Path] = None,
) -> bool:
    identifiers = load_stock_identifiers(stock_identifiers_path)

    if stock_id not in identifiers:
        print(f'Stock ID not found: {stock_id}')
        return False

    stock_item = identifiers[stock_id]
    symbol = stock_item.get('symbol') or stock_id

    print(f'Updating {stock_id} ({symbol})...')

    result = find_ticker_symbol(symbol, exchange, access_token)

    if result.get('found') or result.get('groww_symbol'):
        groww_symbol = result.get('groww_symbol')
        stock_item['groww_symbol'] = groww_symbol
        stock_item['exchange'] = result.get('exchange', exchange)

        if 'quote' in result:
            stock_item['last_quote'] = result['quote']

        print(f'  ✓ Updated: {stock_id} -> {groww_symbol}')
        save_stock_identifiers(identifiers, stock_identifiers_path)
        return True

    print(f'  ✗ Failed: {result.get("error", "Unknown error")}')
    return False


def discover_nse_intraday_symbols(
    exchange: str = 'NSE',
    access_token: Optional[str] = None,
    stock_identifiers_path: Optional[Path] = None,
    limit: Optional[int] = None,
) -> Dict[str, bool]:
    identifiers = load_stock_identifiers(stock_identifiers_path)
    results: Dict[str, bool] = {}
    exchange = exchange.upper()

    instrument_rows = []
    try:
        config = GrowwConfig.from_env() if not access_token else GrowwConfig(access_token=access_token)
        token = access_token or config.access_token
        from growwapi import GrowwAPI

        api = GrowwAPI(token)
        all_instruments = api.get_all_instruments()
        mask = (
            all_instruments['exchange'].astype(str).str.upper() == exchange
            ) & (
            all_instruments['segment'].astype(str).str.upper() == 'CASH'
            ) & (
            all_instruments['is_intraday'].astype(str).str.strip() == '1'
        )
        selected = all_instruments.loc[mask].drop_duplicates(subset='trading_symbol', keep='first')
        if limit is not None:
            selected = selected.head(limit)
        instrument_rows = selected.to_dict('records')
        source = 'api'
        print(f'Found {len(instrument_rows)} intraday-eligible NSE cash instruments from Groww API.')
    except Exception as exc:
        print(f'Failed to load Groww instrument catalog: {exc}')
        print('Falling back to seeded intraday symbol list.')
        source = 'seeded'
        instrument_rows = [
            {
                'trading_symbol': symbol,
                'exchange': exchange,
                'groww_symbol': build_groww_symbol(exchange, symbol),
                'instrument_type': None,
                'series': None,
                'buy_allowed': 1,
                'sell_allowed': 1,
                'is_intraday': 1,
            }
            for symbol in DEFAULT_INTRADAY_SYMBOLS
        ]
        if limit is not None:
            instrument_rows = instrument_rows[:limit]

    for row in instrument_rows:
        trading_symbol = str(row.get('trading_symbol') or row.get('symbol') or '').strip()
        if not trading_symbol:
            continue

        stock_id = trading_symbol.upper()
        existing_entry = identifiers.get(stock_id)
        if existing_entry is None:
            existing_entry = {
                'stock_id': stock_id,
                'symbol': trading_symbol,
                'exchange': exchange,
            }
            identifiers[stock_id] = existing_entry

        existing_entry.update(
            {
                'symbol': trading_symbol,
                'exchange': exchange,
                'groww_symbol': str(row.get('groww_symbol') or build_groww_symbol(exchange, trading_symbol)),
                'active_for_intraday': True,
                'discovery_source': source,
                'instrument_type': row.get('instrument_type'),
                'series': row.get('series'),
                'buy_allowed': int(row['buy_allowed']) if row.get('buy_allowed') is not None else None,
                'sell_allowed': int(row['sell_allowed']) if row.get('sell_allowed') is not None else None,
                'is_intraday': 1 if str(row.get('is_intraday')).strip() == '1' else 0,
            }
        )
        print(f'Discovering {stock_id} ({trading_symbol})...')
        results[stock_id] = True

    save_stock_identifiers(identifiers, stock_identifiers_path)
    print('\nDiscovery Summary:')
    print(f'  Total: {len(results)}')
    print(f'  Added/Updated: {len(results)}')
    return results


def update_all_stock_identifiers(
    exchange: str = 'NSE',
    access_token: Optional[str] = None,
    stock_identifiers_path: Optional[Path] = None,
) -> Dict[str, bool]:
    identifiers = load_stock_identifiers(stock_identifiers_path)
    results = {}

    if not identifiers:
        print('No stock identifiers found; discovering NSE intraday-ready symbols instead.')
        return discover_nse_intraday_symbols(exchange, access_token, stock_identifiers_path)

    print(f'Updating {len(identifiers)} stock identifiers...\n')

    for stock_id in identifiers:
        success = update_stock_identifier(stock_id, exchange, access_token, stock_identifiers_path)
        results[stock_id] = success

    print('\nUpdate Summary:')
    print(f'  Total: {len(results)}')
    print(f'  Successful: {sum(results.values())}')
    print(f'  Failed: {len(results) - sum(results.values())}')

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description='Update stock identifiers with Groww symbol format')
    parser.add_argument('--stock-id', help='Update specific stock ID')
    parser.add_argument('--exchange', default='NSE', choices=['NSE', 'BSE'], help='Stock exchange (default: NSE)')
    parser.add_argument('--file', type=Path, help='Path to stock identifiers JSON file')
    parser.add_argument('--token', help='Groww API access token')
    parser.add_argument('--discover', action='store_true', help='Discover NSE intraday-ready symbols and populate the JSON file')
    parser.add_argument('--limit', type=int, default=None, help='Optional limit for discovery candidates')

    args = parser.parse_args()
    stock_identifiers_path = args.file or STOCK_IDENTIFIERS_FILE

    if args.discover:
        results = discover_nse_intraday_symbols(args.exchange, args.token, stock_identifiers_path, args.limit)
        exit(0 if all(results.values()) or not results else 0)

    if args.stock_id:
        success = update_stock_identifier(args.stock_id, args.exchange, args.token, stock_identifiers_path)
        exit(0 if success else 1)

    results = update_all_stock_identifiers(args.exchange, args.token, stock_identifiers_path)
    exit(0 if all(results.values()) or not results else 0)


if __name__ == '__main__':
    raise SystemExit(main())
