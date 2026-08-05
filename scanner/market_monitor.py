"""Continuous multi-stock bidirectional monitor — no entry price required."""
import argparse
import csv
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from groww_config import GrowwConfig
from rate_limiter import MultiRateLimiter, DEFAULT_GROWW_QUOTE_RATE_LIMITS
from scanner import _extract_quote_value, _to_number, detect_momentum, TRADE_LOG_FILE

DEFAULT_WINDOW_MINUTES = 30.0
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_CONFIRMATION_POLLS = 2
# Absolute buy_qty/sell_qty ratio required to trigger a BUY signal (no warmup needed)
DEFAULT_BUY_RATIO = 2.5
MAX_WORKERS = 6
ALL_SIGNAL_NAMES = ['demand_strong', 'demand_vs_supply', 'breakout', 'bullish_vwap', 'volume_spike']


def _log(message: str) -> None:
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] [market-monitor] {message}', flush=True)


# ---------------------------------------------------------------------------
# Per-stock state
# ---------------------------------------------------------------------------

class StockState:
    """Rolling state tracked independently for each symbol."""

    def __init__(self) -> None:
        # First-poll snapshot used as the SELL baseline — identical to exit_monitor logic
        self.benchmark_ratio: Optional[float] = None
        self.signal_streaks: Dict[str, int] = {name: 0 for name in ALL_SIGNAL_NAMES}


# ---------------------------------------------------------------------------
# Signal detection
# ---------------------------------------------------------------------------

def detect_signals(
    stock_id: str,
    quote: Dict[str, Any],
    state: StockState,
    buy_ratio: float = DEFAULT_BUY_RATIO,
) -> Dict[str, Any]:
    """Return active signals for one quote reading; updates state in place."""
    price = _to_number(_extract_quote_value(quote, 'ltp', 'last_price', 'price', 'close'))
    if price is None:
        return {'price': None, 'signals': [], 'buy_order_percentage': None}

    volume = _to_number(_extract_quote_value(quote, 'volume', 'total_traded_volume', 'total_volume')) or 0.0

    buy_qty = _to_number(_extract_quote_value(quote, 'total_buy_quantity', 'bid_quantity'))
    sell_qty = _to_number(_extract_quote_value(quote, 'total_sell_quantity', 'offer_quantity'))

    signals: List[str] = []
    buy_pct: Optional[float] = None

    if buy_qty and sell_qty and (buy_qty + sell_qty) > 0:
        buy_pct = buy_qty / (buy_qty + sell_qty)

        # SELL: exit_monitor logic — snapshot on first poll, fire when buy_pct drops below it
        if state.benchmark_ratio is None:
            state.benchmark_ratio = buy_pct
        elif buy_pct < state.benchmark_ratio:
            signals.append('demand_vs_supply')

        # BUY: absolute ratio — fires from poll 1, no warmup needed
        if sell_qty > 0 and buy_qty / sell_qty > buy_ratio:
            signals.append('demand_strong')

    # Layer in price-based momentum signals (all BUY-side)
    momentum = detect_momentum(stock_id, quote)
    if momentum:
        for sig in ('breakout', 'bullish_vwap', 'volume_spike'):
            if sig in momentum['signals']:
                signals.append(sig)

    return {
        'price': price,
        'volume': volume,
        'buy_quantity': buy_qty,
        'sell_quantity': sell_qty,
        'buy_order_percentage': buy_pct,
        'signals': signals,
    }


# ---------------------------------------------------------------------------
# Quote fetching
# ---------------------------------------------------------------------------

def _build_fetcher(
    stock_ids: List[str],
) -> Optional[Callable[[], Dict[str, Optional[Dict[str, Any]]]]]:
    config = GrowwConfig.from_env()
    if not config.access_token:
        _log('Groww access token not set in .env')
        return None

    try:
        from growwapi import GrowwAPI
        groww = GrowwAPI(config.access_token)
    except Exception as exc:
        _log(f'Failed to initialize GrowwAPI: {exc}')
        return None

    rate_limiter = MultiRateLimiter(DEFAULT_GROWW_QUOTE_RATE_LIMITS)

    def _fetch_one(symbol: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        try:
            with rate_limiter:
                return symbol, groww.get_quote(
                    exchange=groww.EXCHANGE_NSE,
                    segment=groww.SEGMENT_CASH,
                    trading_symbol=symbol,
                )
        except Exception as exc:
            _log(f'Quote fetch failed for {symbol}: {exc}')
            return symbol, None

    def _fetch_all() -> Dict[str, Optional[Dict[str, Any]]]:
        results: Dict[str, Optional[Dict[str, Any]]] = {}
        workers = max(1, min(MAX_WORKERS, len(stock_ids)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_one, sym): sym for sym in stock_ids}
            for future in as_completed(futures):
                sym, quote = future.result()
                results[sym] = quote
        return results

    return _fetch_all


# ---------------------------------------------------------------------------
# Main monitoring loop
# ---------------------------------------------------------------------------

def run_market_monitor(
    stock_ids: List[str],
    window_minutes: float = DEFAULT_WINDOW_MINUTES,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    confirmation_polls: int = DEFAULT_CONFIRMATION_POLLS,
    buy_ratio: float = DEFAULT_BUY_RATIO,
    fetch_quotes_fn: Optional[Callable[[], Dict[str, Optional[Dict[str, Any]]]]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
) -> int:
    if not stock_ids:
        _log('No stock IDs provided.')
        return 0

    if fetch_quotes_fn is None:
        fetch_quotes_fn = _build_fetcher(stock_ids)
        if fetch_quotes_fn is None:
            _log('No quote source available. Check Groww credentials.')
            return 0

    states: Dict[str, StockState] = {sym: StockState() for sym in stock_ids}
    deadline = now_fn() + window_minutes * 60

    _log(
        f'Watching {len(stock_ids)} symbol(s) for {window_minutes:.1f} min | '
        f'buy_ratio={buy_ratio} | confirm={confirmation_polls} polls'
    )

    while now_fn() < deadline:
        quotes = fetch_quotes_fn()

        for symbol, quote in quotes.items():
            if quote is None:
                continue

            state = states[symbol]
            reading = detect_signals(symbol, quote, state, buy_ratio)
            price = reading['price']
            if price is None:
                continue

            active_signals = reading['signals']
            confirmed: List[str] = []

            for name in ALL_SIGNAL_NAMES:
                if name in active_signals:
                    state.signal_streaks[name] += 1
                else:
                    state.signal_streaks[name] = 0

                if state.signal_streaks[name] >= confirmation_polls:
                    confirmed.append(name)

            if confirmed:
                direction = 'BUY' if any(s in confirmed for s in ('demand_strong', 'breakout', 'bullish_vwap', 'volume_spike')) else 'SELL'
                buy_pct = reading['buy_order_percentage']
                pct_note = f' buy_pct={buy_pct:.2%}' if buy_pct is not None else ''
                _log(f"ALERT {direction} {symbol}: price={price}{pct_note} signals={', '.join(confirmed)}")
            else:
                pending = [
                    f'{n}({state.signal_streaks[n]}/{confirmation_polls})'
                    for n in active_signals
                ]
                note = f" pending: {', '.join(pending)}" if pending else ''
                _log(f'{symbol} holding: price={price}{note}')

        if now_fn() < deadline:
            sleep_fn(poll_interval_seconds)

    _log('Monitoring window closed.')
    return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_watchlist(symbols_arg: Optional[List[str]], top_n: int) -> List[str]:
    """Resolve watchlist: explicit symbols, or unique stock_ids from trade_logs.csv."""
    if symbols_arg:
        return [s.strip().upper() for s in symbols_arg if s.strip()]
    if TRADE_LOG_FILE.exists() and TRADE_LOG_FILE.stat().st_size > 0:
        seen: dict = {}
        with TRADE_LOG_FILE.open('r', encoding='utf-8', newline='') as f:
            for row in csv.DictReader(f):
                sym = (row.get('stock_id') or '').strip().upper()
                if sym:
                    seen[sym] = None  # preserve insertion order, deduplicate
        symbols = list(seen.keys())[:top_n]
        if symbols:
            return symbols
    _log('trade_logs.csv is empty or missing; pass --symbols explicitly.')
    return []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Bidirectional market monitor — alerts BUY/SELL based on live order book and momentum, no entry price needed.',
    )
    parser.add_argument('--symbols', nargs='+', metavar='SYM', help='Space-separated symbols to watch (default: top-N from stock_identifiers.json)')
    parser.add_argument('--top-n', type=int, default=20, help='How many symbols to load when --symbols is omitted')
    parser.add_argument('--window-minutes', type=float, default=DEFAULT_WINDOW_MINUTES)
    parser.add_argument('--poll-interval', type=float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    parser.add_argument('--confirmation-polls', type=int, default=DEFAULT_CONFIRMATION_POLLS)
    parser.add_argument('--buy-ratio', type=float, default=DEFAULT_BUY_RATIO, help='Minimum buy_qty/sell_qty ratio to trigger a BUY signal (default: 2.5)')
    return parser


def main() -> int:
    args = build_parser().parse_args()
    stock_ids = _load_watchlist(args.symbols, args.top_n)
    if not stock_ids:
        return 1
    return run_market_monitor(
        stock_ids=stock_ids,
        window_minutes=args.window_minutes,
        poll_interval_seconds=args.poll_interval,
        confirmation_polls=args.confirmation_polls,
        buy_ratio=args.buy_ratio,
    )


if __name__ == '__main__':
    raise SystemExit(main())
