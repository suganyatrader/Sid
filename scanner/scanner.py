import csv
import datetime
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
STOCK_IDENTIFIERS_FILE = DATA_DIR / 'stock_identifiers.json'
LIVE_QUOTES_FILE = DATA_DIR / 'live_quotes.json'
TRADE_LOG_FILE = DATA_DIR / 'trade_logs.csv'


def load_stock_identifiers():
    if not STOCK_IDENTIFIERS_FILE.exists():
        return {}
    with STOCK_IDENTIFIERS_FILE.open('r', encoding='utf-8') as f:
        return {item['stock_id']: item for item in json.load(f)}


def load_live_quotes():
    if not LIVE_QUOTES_FILE.exists():
        return {}
    with LIVE_QUOTES_FILE.open('r', encoding='utf-8') as f:
        return json.load(f)


def detect_momentum(stock_id, quote):
    price = quote.get('ltp')
    if price is None:
        return None

    previous_close = quote.get('previous_close') or quote.get('pc') or 0
    volume = quote.get('volume') or quote.get('total_traded_volume') or 0
    vwap = quote.get('vwap') or quote.get('vw') or None

    signals = []
    if previous_close and price > previous_close * 1.01:
        signals.append('breakout')
    if vwap and price > vwap:
        signals.append('bullish_vwap')
    if volume and previous_close and price > previous_close and volume > 0:
        signals.append('momentum_volume')

    return {
        'stock_id': stock_id,
        'price': price,
        'previous_close': previous_close,
        'vwap': vwap,
        'volume': volume,
        'signals': signals
    }


def append_trade_log(stock_id, action, price, volume, pnl):
    header = ['trade_id', 'stock_id', 'action', 'price', 'volume', 'timestamp', 'pnl']
    TRADE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    next_id = 1

    if TRADE_LOG_FILE.exists():
        with TRADE_LOG_FILE.open('r', encoding='utf-8', newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)
            if rows:
                next_id = int(rows[-1]['trade_id']) + 1

    with TRADE_LOG_FILE.open('a', encoding='utf-8', newline='') as csvfile:
        writer = csv.writer(csvfile)
        if next_id == 1:
            writer.writerow(header)
        writer.writerow([next_id, stock_id, action, price, volume, datetime.datetime.now().isoformat(), pnl])


def run_scanner():
    stock_identifiers = load_stock_identifiers()
    live_quotes = load_live_quotes()

    if not live_quotes:
        print('[scanner] No live quotes available. Run ingestion first.')
        return

    for stock_id, payload in live_quotes.items():
        quote = payload.get('quote') or payload
        result = detect_momentum(stock_id, quote)
        if not result:
            continue

        name = stock_identifiers.get(stock_id, {}).get('symbol', stock_id)
        signals = result['signals']
        if not signals:
            print(f'[scanner] {stock_id} ({name}) - no momentum signal')
            continue

        print(f"[scanner] {stock_id} ({name}) - signals: {', '.join(signals)} | price={result['price']} vwap={result['vwap']} volume={result['volume']}")

        action = 'BUY' if 'breakout' in signals or 'bullish_vwap' in signals else 'HOLD'
        if action == 'BUY':
            volume = 1
            pnl = 0.0
            append_trade_log(stock_id, action, result['price'], volume, pnl)
            print(f'[scanner] Simulated {action} for {stock_id} at {result[