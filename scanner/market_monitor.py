# Continuous multi-stock BUY/SELL monitor: polls live order-book and price data at a
# configurable interval, fires confirmed signals (demand_strong, breakout, bullish_vwap,
# volume_spike for BUY; demand_vs_supply, price_reversal for SELL) with streak-based
# confirmation to suppress noise. Thresholds calibrated for ₹20k×5x margin, ₹2k target.
import argparse
import csv
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from groww_config import GrowwConfig
from rate_limiter import MultiRateLimiter, DEFAULT_GROWW_QUOTE_RATE_LIMITS
from scanner import _extract_quote_value, _to_number, detect_momentum, TRADE_LOG_FILE

DEFAULT_WINDOW_MINUTES = 30.0
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_CONFIRMATION_POLLS = 2
DEFAULT_BUY_RATIO = 2.5
# LTP in upper 60% of bid-ask spread → buyer-initiated; lower 40% → seller-initiated
SPREAD_BUY_THRESHOLD = 0.6
SPREAD_SELL_THRESHOLD = 0.4
# 65%+ of top-5 depth value on one side confirms the direction
DEPTH_IMBALANCE_THRESHOLD = 0.65
# Price trend: compare LTP to 10 polls ago; 0.1% move required to signal trend
PRICE_TREND_WINDOW = 10
PRICE_TREND_MIN_DELTA = 0.001
# 2% = ₹2,000 target on ₹1,00,000 effective position (₹20k × 5x margin)
DEFAULT_REVERSAL_THRESHOLD = 0.02
MAX_WORKERS = 6
# Lower weight for breakout/vwap — they are lagging (compare to prev close, not real-time)
SIGNAL_WEIGHTS: Dict[str, int] = {
    'demand_strong':    3,
    'demand_vs_supply': -3,
    'price_trend_up':   2,
    'price_trend_down': -2,
    'breakout':         1,
    'bullish_vwap':     1,
    'volume_spike':     1,
    'price_reversal':   -3,
}


def _log(message: str) -> None:
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] [market-monitor] {message}', flush=True)


def _extract_depth(quote: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], float, float]:
    """Return (best_bid, best_ask, bid_depth_value, ask_depth_value) from order book depth."""
    depth = None
    if isinstance(quote, dict):
        depth = quote.get('depth')
        if not isinstance(depth, dict):
            for nested_key in ('last_quote', 'quote', 'market'):
                nested = quote.get(nested_key)
                if isinstance(nested, dict):
                    depth = nested.get('depth')
                    if isinstance(depth, dict):
                        break

    if not isinstance(depth, dict):
        return None, None, 0.0, 0.0

    buy_levels = depth.get('buy') or []
    sell_levels = depth.get('sell') or []

    best_bid = _to_number(buy_levels[0].get('price')) if buy_levels and isinstance(buy_levels[0], dict) else None
    best_ask = _to_number(sell_levels[0].get('price')) if sell_levels and isinstance(sell_levels[0], dict) else None

    bid_value = sum(
        (_to_number(lvl.get('price')) or 0) * (_to_number(lvl.get('quantity')) or 0)
        for lvl in buy_levels if isinstance(lvl, dict)
    )
    ask_value = sum(
        (_to_number(lvl.get('price')) or 0) * (_to_number(lvl.get('quantity')) or 0)
        for lvl in sell_levels if isinstance(lvl, dict)
    )
    return best_bid, best_ask, bid_value, ask_value


# ---------------------------------------------------------------------------
# Per-stock state
# ---------------------------------------------------------------------------

class StockState:
    """Rolling state tracked independently for each symbol."""

    def __init__(self) -> None:
        self.price_window: deque = deque(maxlen=PRICE_TREND_WINDOW + 1)
        self.price_high: Optional[float] = None
        self.bull_streak: int = 0
        self.bear_streak: int = 0


# ---------------------------------------------------------------------------
# Signal detection
# ---------------------------------------------------------------------------

def detect_signals(
    stock_id: str,
    quote: Dict[str, Any],
    state: StockState,
    buy_ratio: float = DEFAULT_BUY_RATIO,
    reversal_threshold: float = DEFAULT_REVERSAL_THRESHOLD,
) -> Dict[str, Any]:
    """Return active signals for one quote reading; updates state in place."""
    price = _to_number(_extract_quote_value(quote, 'ltp', 'last_price', 'price', 'close'))
    if price is None:
        return {'price': None, 'signals': [], 'buy_order_percentage': None}

    state.price_window.append(price)

    volume = _to_number(_extract_quote_value(quote, 'volume', 'total_traded_volume', 'total_volume')) or 0.0

    buy_qty = _to_number(_extract_quote_value(quote, 'total_buy_quantity', 'bid_quantity'))
    sell_qty = _to_number(_extract_quote_value(quote, 'total_sell_quantity', 'offer_quantity'))

    signals: List[str] = []
    buy_pct: Optional[float] = None
    buy_sell_ratio: Optional[float] = None

    if buy_qty and sell_qty and (buy_qty + sell_qty) > 0:
        buy_pct = buy_qty / (buy_qty + sell_qty)
        buy_sell_ratio = buy_qty / sell_qty
        # Absolute ratio fallback — fires even without depth data
        if buy_sell_ratio > buy_ratio:
            signals.append('demand_strong')

    # Order-book positioning: where is LTP relative to the bid-ask spread?
    best_bid, best_ask, bid_depth_value, ask_depth_value = _extract_depth(quote)
    spread_position: Optional[float] = None
    depth_bid_share: Optional[float] = None

    if best_bid is not None and best_ask is not None and best_ask > best_bid:
        spread = best_ask - best_bid
        spread_position = (price - best_bid) / spread  # 0=at bid, 1=at ask

        # LTP in upper portion of spread → buyer lifted the ask → buying pressure
        if spread_position >= SPREAD_BUY_THRESHOLD:
            if 'demand_strong' not in signals:
                signals.append('demand_strong')
        # LTP in lower portion of spread → seller hit the bid → selling pressure
        elif spread_position <= SPREAD_SELL_THRESHOLD:
            signals.append('demand_vs_supply')

        total_depth = bid_depth_value + ask_depth_value
        if total_depth > 0:
            depth_bid_share = bid_depth_value / total_depth
            # Confirm direction with depth value imbalance
            if depth_bid_share > DEPTH_IMBALANCE_THRESHOLD and 'demand_strong' not in signals:
                signals.append('demand_strong')
            elif depth_bid_share < (1 - DEPTH_IMBALANCE_THRESHOLD) and 'demand_vs_supply' not in signals:
                signals.append('demand_vs_supply')

    # Layer in price-based momentum signals (all BUY-side)
    momentum = detect_momentum(stock_id, quote)
    if momentum:
        for sig in ('breakout', 'bullish_vwap', 'volume_spike'):
            if sig in momentum['signals']:
                signals.append(sig)

    # SELL: fire when price drops reversal_threshold below the rolling session high
    if state.price_high is None or price > state.price_high:
        state.price_high = price
    elif state.price_high > 0 and price < state.price_high * (1 - reversal_threshold):
        signals.append('price_reversal')

    # Short-term price trend: compare current price to PRICE_TREND_WINDOW polls ago
    if len(state.price_window) > PRICE_TREND_WINDOW:
        ref_price = state.price_window[0]
        if ref_price and ref_price > 0:
            delta = (price - ref_price) / ref_price
            if delta > PRICE_TREND_MIN_DELTA:
                signals.append('price_trend_up')
            elif delta < -PRICE_TREND_MIN_DELTA:
                signals.append('price_trend_down')

    price_drawdown_pct = (
        (state.price_high - price) / state.price_high
        if state.price_high and state.price_high > 0
        else None
    )

    return {
        'price': price,
        'volume': volume,
        'buy_quantity': buy_qty,
        'sell_quantity': sell_qty,
        'buy_order_percentage': buy_pct,
        'buy_sell_ratio': buy_sell_ratio,
        'best_bid': best_bid,
        'best_ask': best_ask,
        'spread_position': spread_position,
        'depth_bid_share': depth_bid_share,
        'price_high': state.price_high,
        'price_drawdown_percentage': price_drawdown_pct,
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
    reversal_threshold: float = DEFAULT_REVERSAL_THRESHOLD,
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
            reading = detect_signals(symbol, quote, state, buy_ratio, reversal_threshold)
            price = reading['price']
            if price is None:
                continue

            active_signals = reading['signals']

            short_trend_parts = []
            buy_pct = reading['buy_order_percentage']
            buy_sell_ratio = reading['buy_sell_ratio']
            best_bid = reading['best_bid']
            best_ask = reading['best_ask']
            spread_position = reading['spread_position']
            depth_bid_share = reading['depth_bid_share']
            drawdown_pct = reading['price_drawdown_percentage']
            price_high = reading['price_high']
            if best_bid is not None and best_ask is not None and best_bid > 0 and best_ask > 0:
                spread = best_ask - best_bid
                pos_note = f' pos={spread_position:.2f}' if spread_position is not None else ''
                short_trend_parts.append(f'bid={best_bid} ask={best_ask} spread={spread:.2f}{pos_note}')
            if depth_bid_share is not None:
                short_trend_parts.append(f'depth_bid={depth_bid_share:.0%}')
            if buy_sell_ratio is not None:
                short_trend_parts.append(f'buy_sell_ratio={buy_sell_ratio:.2f}')
            if drawdown_pct is not None and price_high is not None and drawdown_pct > 0.001:
                short_trend_parts.append(f'high={price_high} drawdown={drawdown_pct:.2%}')
            short_trend_note = (
                f" short_trend: {' | '.join(short_trend_parts)}"
                if short_trend_parts
                else ''
            )

            # Weighted score: positive = bullish, negative = bearish
            score = sum(SIGNAL_WEIGHTS.get(s, 0) for s in active_signals)

            if score > 0:
                state.bull_streak += 1
                state.bear_streak = 0
            elif score < 0:
                state.bear_streak += 1
                state.bull_streak = 0
            else:
                state.bull_streak = 0
                state.bear_streak = 0

            score_note = f' score={score:+d}'
            if state.bull_streak >= confirmation_polls:
                pct_note = f' buy_pct={buy_pct:.2%}' if buy_pct is not None else ''
                _log(
                    f"ALERT BUY {symbol}: price={price}{pct_note}{score_note} "
                    f"signals={', '.join(active_signals)}{short_trend_note}"
                )
            elif state.bear_streak >= confirmation_polls:
                pct_note = f' buy_pct={buy_pct:.2%}' if buy_pct is not None else ''
                _log(
                    f"ALERT SELL {symbol}: price={price}{pct_note}{score_note} "
                    f"signals={', '.join(active_signals)}{short_trend_note}"
                )
            else:
                pending_streak = state.bull_streak if score > 0 else state.bear_streak
                direction_label = 'BUY' if score > 0 else 'SELL' if score < 0 else 'NEUTRAL'
                note = f" pending {direction_label}({pending_streak}/{confirmation_polls}): {', '.join(active_signals)}" if active_signals else ''
                _log(f'{symbol} holding: price={price}{score_note}{note}{short_trend_note}')

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
    parser.add_argument('--reversal-threshold', type=float, default=DEFAULT_REVERSAL_THRESHOLD, help='%% drop from rolling price high to fire price_reversal SELL (default: 0.02 = 2%%)')
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
        reversal_threshold=args.reversal_threshold,
    )


if __name__ == '__main__':
    raise SystemExit(main())
