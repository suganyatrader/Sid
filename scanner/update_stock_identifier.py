"""
Update stock identifiers with Groww symbol format.

This script finds the ticker symbol and updates the stock identifier JSON file
with the proper Groww symbol format (e.g., NSE-WIPRO, BSE-RELIANCE).

Groww Symbol Format:
- Stocks: EXCHANGE-TRADING_SYMBOL (e.g., NSE-WIPRO)
- Futures: EXCHANGE-SYMBOL-EXPIRYDATE-FUT (e.g., NSE-NIFTY-30Sep25-FUT)
- Options: EXCHANGE-SYMBOL-EXPIRYDATE-STRIKE-CE/PE
"""

import json
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional

from growwapi import GrowwAPI
from groww_config import GrowwConfig


DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
STOCK_IDENTIFIERS_FILE = DATA_DIR / 'stock_identifiers.json'


def build_groww_symbol(
    exchange: str,
    trading_symbol: str,
    expiry_date: Optional[str] = None,
    strike_price: Optional[float] = None,
    option_type: Optional[str] = None,
) -> str:
    """
    Build a Groww symbol according to the specification.
    
    Args:
        exchange: Stock exchange (NSE, BSE)
        trading_symbol: Trading symbol/ticker (e.g., WIPRO, NIFTY)
        expiry_date: Expiry date for derivatives (format: DDMmmYY, e.g., 23Jan25)
        strike_price: Strike price for options
        option_type: Option type (CE or PE)
    
    Returns:
        Groww symbol string
    """
    components = [exchange, trading_symbol]
    
    if expiry_date:
        components.append(expiry_date)
    
    if strike_price is not None:
        components.append(str(int(strike_price)))
    
    if option_type:
        components.append(option_type)
    
    return '-'.join(components)


def find_ticker_symbol(
    symbol: str,
    exchange: str = 'NSE',
    access_token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Find ticker symbol information from Groww API.
    
    Args:
        symbol: Trading symbol to search for
        exchange: Stock exchange (NSE or BSE)
        access_token: Groww API access token
    
    Returns:
        Dictionary with symbol information including groww_symbol
    """
    try:
        config = GrowwConfig.from_env() if not access_token else GrowwConfig(access_token=access_token)
        token = access_token or config.get_access_token()
        
        if not token:
            raise ValueError('Access token not found')
        
        groww = GrowwAPI(token)
        
        # Try NSE first, then BSE if not found
        exchanges_to_try = [exchange]
        if exchange.upper() == 'NSE':
            exchanges_to_try.append('BSE')
        elif exchange.upper() == 'BSE':
            exchanges_to_try.append('NSE')
        
        for exch in exchanges_to_try:
            try:
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
            except Exception as e:
                continue
        
        # If not found in API, construct the Groww symbol anyway
        groww_symbol = build_groww_symbol(exchange, symbol)
        return {
            'trading_symbol': symbol,
            'exchange': exchange,
            'groww_symbol': groww_symbol,
            'found': False,
            'error': 'Symbol not found in API, but Groww symbol constructed',
        }
    
    except Exception as e:
        return {
            'trading_symbol': symbol,
            'error': str(e),
            'found': False,
        }


def load_stock_identifiers(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Load stock identifiers from JSON file."""
    target = path or STOCK_IDENTIFIERS_FILE
    
    if not target.exists():
        print(f'File not found: {target}')
        return {}
    
    with target.open('r', encoding='utf-8') as handle:
        payload = json.load(handle)
    
    if isinstance(payload, dict):
        return {str(item_id): item for item_id, item in payload.items()}
    
    return {str(item.get('stock_id', idx)): item for idx, item in enumerate(payload) if isinstance(item, dict)}


def save_stock_identifiers(
    identifiers: Dict[str, Dict[str, Any]],
    path: Optional[Path] = None,
) -> bool:
    """Save stock identifiers to JSON file."""
    target = path or STOCK_IDENTIFIERS_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    
    # Convert dict to list format if needed
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
    """
    Update a single stock identifier with Groww symbol.
    
    Args:
        stock_id: Stock ID to update
        exchange: Stock exchange (NSE or BSE)
        access_token: Groww API access token
        stock_identifiers_path: Path to stock identifiers JSON file
    
    Returns:
        True if successful, False otherwise
    """
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
    else:
        print(f'  ✗ Failed: {result.get("error", "Unknown error")}')
        return False


def update_all_stock_identifiers(
    exchange: str = 'NSE',
    access_token: Optional[str] = None,
    stock_identifiers_path: Optional[Path] = None,
) -> Dict[str, bool]:
    """
    Update all stock identifiers with Groww symbols.
    
    Args:
        exchange: Stock exchange (NSE or BSE)
        access_token: Groww API access token
        stock_identifiers_path: Path to stock identifiers JSON file
    
    Returns:
        Dictionary with update status for each stock
    """
    identifiers = load_stock_identifiers(stock_identifiers_path)
    results = {}
    
    if not identifiers:
        print('No stock identifiers found')
        return results
    
    print(f'Updating {len(identifiers)} stock identifiers...\n')
    
    for stock_id in identifiers:
        success = update_stock_identifier(
            stock_id,
            exchange,
            access_token,
            stock_identifiers_path,
        )
        results[stock_id] = success
    
    print(f'\nUpdate Summary:')
    print(f'  Total: {len(results)}')
    print(f'  Successful: {sum(results.values())}')
    print(f'  Failed: {len(results) - sum(results.values())}')
    
    return results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Update stock identifiers with Groww symbol format'
    )
    parser.add_argument(
        '--stock-id',
        help='Update specific stock ID',
    )
    parser.add_argument(
        '--exchange',
        default='NSE',
        choices=['NSE', 'BSE'],
        help='Stock exchange (default: NSE)',
    )
    parser.add_argument(
        '--file',
        type=Path,
        help='Path to stock identifiers JSON file',
    )
    parser.add_argument(
        '--token',
        help='Groww API access token',
    )
    
    args = parser.parse_args()
    
    stock_identifiers_path = args.file or STOCK_IDENTIFIERS_FILE
    
    if args.stock_id:
        success = update_stock_identifier(
            args.stock_id,
            args.exchange,
            args.token,
            stock_identifiers_path,
        )
        exit(0 if success else 1)
    else:
        results = update_all_stock_identifiers(
            args.exchange,
            args.token,
            stock_identifiers_path,
        )
        exit(0 if all(results.values()) else 1)


if __name__ == '__main__':
    main()
