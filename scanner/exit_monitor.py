import argparse
import csv
import datetime
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from groww_config import GrowwConfig
from rate_limiter import MultiRateLimiter, DEFAULT_GROWW_QUOTE_RATE_LIMITS
from scanner import _extract_quote_value, _to_number

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
EXIT_ALERTS_FILE = DATA_DIR / 'exit_alerts.csv'

DEFAULT_WINDOW_MINUTES = 10.0
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_CONFIRMATION_POLLS = 2
DEFAULT_DEMAND_SUPPLY_RATIO = 1.2
VOLUME_DELTA_HISTORY_SIZE = 20

ALL_SIGNAL_NAMES = ['demand_vs_supply']


def _extract_buy_sell_quantities(quote: Dict[str, Any]) -> tuple:
    buy_quantity = _to_number(_extract_quote_value(quote, 'total_buy_quantity'))
    sell_quantity = _to_number(_extract_quote_value(quote, 'total_sell_quantity'))
    if buy_quantity is None or sell_quantity is None:
        buy_quantity = _to_number(_extract_quote_value(quote, 'bid_quantity'))
        sell_quantity = _to_number(_extract_quote_value(quote, 'offer_quantity'))
    return buy_quantity, sell_quantity


def detect_reversal(
    quote: Dict[str, Any],
    entry_price: float = 0.0,
    peak_price: float = 0.0,
    previous_price: Optional[float] = None,
    previous_volume: Optional[float] = None,
    benchmark_ratio: Optional[float] = None,
) -> Dict[str, Any]:
    price = _to_number(_extract_quote_value(quote, 'ltp', 'last_price', 'price', 'close'))
    if price is None:
        return {'price': None, 'volume': None, 'peak_price': peak_price, 'signals': []}

    volume = _to_number(_extract_quote_value(quote, 'volume', 'total_traded_volume', 'total_volume')) or 0.0
    buy_quantity, sell_quantity = _extract_buy_sell_quantities(quote)

    peak_price = max(peak_price, price)

    signals: List[str] = []
    ratio = (buy_quantity / sell_quantity) if buy_quantity and sell_quantity else None
    if ratio is not None and benchmark_ratio is not None and ratio < benchmark_ratio:
        signals.append('demand_vs_supply')

    return {
        'price': price,
        'volume': volume,
        'buy_quantity': buy_quantity,
        'sell_quantity': sell_quantity,
        'peak_price': peak_price,
        'demand_supply_ratio': ratio,
        'signals': signals,
    }


def append_exit_alert(
    stock_id: str,
    price: float,
    entry_price: float,
    peak_price: float,
    signals: List[str],
    path: Optional[Path] = None,
) -> None:
    target = path or EXIT_ALERTS_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    header = ['timestamp', 'stock_id', 'price', 'entry_price', 'peak_price', 'signals']
    write_header = not target.exists() or target.stat().st_size == 0

    with target.open('a', encoding='utf-8', newline='') as csvfile:
        writer = csv.writer(csvfile)
        if write_header:
            writer.writerow(header)
        writer.writerow([
            datetime.datetime.now().isoformat(),
            stock_id,
            price,
            entry_price,
            peak_price,
            ';'.join(signals),
        ])


def _build_live_quote_fetcher(stock_id: str) -> Optional[Callable[[], Optional[Dict[str, Any]]]]:
    config = GrowwConfig.from_env()
    access_token = config.access_token
    if not access_token:
        print('[exit-monitor] Groww access token not set in .env')
        return None

    try:
        from growwapi import GrowwAPI

        groww = GrowwAPI(access_token)
    except Exception as exc:
        print(f'[exit-monitor] Failed to initialize GrowwAPI client: {exc}')
        return None

    rate_limiter = MultiRateLimiter(DEFAULT_GROWW_QUOTE_RATE_LIMITS)

    def _fetch() -> Optional[Dict[str, Any]]:
        try:
            with rate_limiter:
                return groww.get_quote(
                    exchange=groww.EXCHANGE_NSE,
                    segment=groww.SEGMENT_CASH,
                    trading_symbol=stock_id,
                )
        except Exception as exc:
            print(f'[exit-monitor] Failed to fetch quote for {stock_id}: {exc}')
            return None

    return _fetch


def run_exit_monitor(
    stock_id: str,
    entry_price: float = 0.0,
    window_minutes: float = DEFAULT_WINDOW_MINUTES,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    confirmation_polls: int = DEFAULT_CONFIRMATION_POLLS,
    demand_supply_ratio: float = DEFAULT_DEMAND_SUPPLY_RATIO,
    alerts_path: Optional[Path] = None,
    fetch_quote_fn: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
) -> int:
    if fetch_quote_fn is None:
        fetch_quote_fn = _build_live_quote_fetcher(stock_id)
        if fetch_quote_fn is None:
            print('[exit-monitor] No quote source available. Check Groww credentials.')
            return 0

    deadline = now_fn() + window_minutes * 60
    peak_price = entry_price
    previous_price: Optional[float] = None
    previous_volume: Optional[float] = None
    volume_deltas: List[float] = []
    signal_streaks = {name: 0 for name in ALL_SIGNAL_NAMES}
    benchmark_ratio: Optional[float] = None

    print(
        f'[exit-monitor] Watching {stock_id} for {window_minutes:.1f} min, entry_price={entry_price}, '
        f'confirming signals over {confirmation_polls} consecutive polls'
    )

    while now_fn() < deadline:
        quote = fetch_quote_fn()
        if quote is None:
            print(f'[exit-monitor] Failed to fetch quote for {stock_id}, skipping this poll')
            sleep_fn(poll_interval_seconds)
            continue

        average_volume_delta = (sum(volume_deltas) / len(volume_deltas)) if volume_deltas else None

        if benchmark_ratio is None:
            buy_quantity, sell_quantity = _extract_buy_sell_quantities(quote)
            if buy_quantity and sell_quantity:
                benchmark_ratio = buy_quantity / sell_quantity
                print(f'[exit-monitor] Benchmark ratio for {stock_id}: {benchmark_ratio:.2f}')
            else:
                benchmark_ratio = demand_supply_ratio

        reading = detect_reversal(
            quote,
            entry_price,
            peak_price,
            previous_price,
            previous_volume,
            benchmark_ratio=benchmark_ratio,
        )
        price = reading['price']
        if price is None:
            print(f'[exit-monitor] Quote for {stock_id} had no usable price, skipping this poll')
            sleep_fn(poll_interval_seconds)
            continue

        peak_price = reading['peak_price']
        active_signals = reading['signals']

        confirmed_signals = []
        for name in ALL_SIGNAL_NAMES:
            if name in active_signals:
                signal_streaks[name] += 1
            else:
                signal_streaks[name] = 0
            if signal_streaks[name] >= confirmation_polls:
                confirmed_signals.append(name)

        if confirmed_signals:
            print(
                f"[exit-monitor] ALERT sell {stock_id}: price={price} peak={peak_price} "
                f"signals={', '.join(confirmed_signals)}"
            )
            append_exit_alert(stock_id, price, entry_price, peak_price, confirmed_signals, alerts_path)
        else:
            pending = [f'{name}({signal_streaks[name]}/{confirmation_polls})' for name in active_signals]
            pending_note = f" pending: {', '.join(pending)}" if pending else ''
            print(f'[exit-monitor] {stock_id} holding: price={price} peak={peak_price}{pending_note}')

        if reading['volume'] is not None and previous_volume is not None:
            delta = reading['volume'] - previous_volume
            if delta >= 0:
                volume_deltas.append(delta)
                volume_deltas = volume_deltas[-VOLUME_DELTA_HISTORY_SIZE:]

        previous_price = price
        previous_volume = reading['volume']

        if now_fn() < deadline:
            sleep_fn(poll_interval_seconds)

    print(f'[exit-monitor] Monitoring window closed for {stock_id}')
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Watch a ticker and alert when its demand-vs-supply ratio weakens from the first benchmark')
    parser.add_argument('--stock-id', required=True, help='Trading symbol to watch')
    parser.add_argument('--window-minutes', type=float, default=DEFAULT_WINDOW_MINUTES, help='How long to keep watching')
    parser.add_argument('--poll-interval', type=float, default=DEFAULT_POLL_INTERVAL_SECONDS, help='Seconds between quote polls')
    parser.add_argument('--confirmation-polls', type=int, default=DEFAULT_CONFIRMATION_POLLS, help='Consecutive polls a signal must hold before it triggers an alert')
    parser.add_argument('--demand-supply-ratio', type=float, default=DEFAULT_DEMAND_SUPPLY_RATIO, help='Fallback ratio used if the first quote has no usable order-book quantities')
    parser.add_argument('--exit-alerts-file', type=Path, default=EXIT_ALERTS_FILE, help='CSV file to log alerts to')
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run_exit_monitor(
        stock_id=args.stock_id,
        window_minutes=args.window_minutes,
        poll_interval_seconds=args.poll_interval,
        confirmation_polls=args.confirmation_polls,
        demand_supply_ratio=args.demand_supply_ratio,
        alerts_path=args.exit_alerts_file,
    )


if __name__ == '__main__':
    raise SystemExit(main())
