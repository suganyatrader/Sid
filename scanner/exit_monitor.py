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
DEFAULT_POLL_INTERVAL_SECONDS = 15.0
DEFAULT_TRAILING_STOP_PCT = 0.5
DEFAULT_VOLUME_SPIKE_MULTIPLIER = 1.5
DEFAULT_SUPPLY_PRESSURE_RATIO = 1.2


def _extract_buy_sell_quantities(quote: Dict[str, Any]) -> tuple:
    buy_quantity = _to_number(_extract_quote_value(quote, 'total_buy_quantity'))
    sell_quantity = _to_number(_extract_quote_value(quote, 'total_sell_quantity'))
    if buy_quantity is None or sell_quantity is None:
        buy_quantity = _to_number(_extract_quote_value(quote, 'bid_quantity'))
        sell_quantity = _to_number(_extract_quote_value(quote, 'offer_quantity'))
    return buy_quantity, sell_quantity


def detect_reversal(
    quote: Dict[str, Any],
    entry_price: float,
    peak_price: float,
    previous_price: Optional[float],
    previous_volume: Optional[float],
    trailing_stop_pct: float = DEFAULT_TRAILING_STOP_PCT,
    volume_spike_multiplier: float = DEFAULT_VOLUME_SPIKE_MULTIPLIER,
    supply_pressure_ratio: float = DEFAULT_SUPPLY_PRESSURE_RATIO,
) -> Dict[str, Any]:
    price = _to_number(_extract_quote_value(quote, 'ltp', 'last_price', 'price', 'close'))
    if price is None:
        return {'price': None, 'volume': None, 'peak_price': peak_price, 'signals': []}

    vwap = _to_number(_extract_quote_value(quote, 'vwap', 'vw', 'average_price'))
    volume = _to_number(_extract_quote_value(quote, 'volume', 'total_traded_volume', 'total_volume')) or 0.0
    buy_quantity, sell_quantity = _extract_buy_sell_quantities(quote)

    peak_price = max(peak_price, price)

    signals: List[str] = []
    if price < entry_price:
        signals.append('below_entry')
    if vwap and price < vwap:
        signals.append('below_vwap')
    if peak_price and price <= peak_price * (1 - trailing_stop_pct / 100.0):
        signals.append('trailing_stop')
    if (
        previous_price is not None
        and previous_volume
        and price < previous_price
        and volume > previous_volume * volume_spike_multiplier
    ):
        signals.append('down_volume_spike')
    if buy_quantity and sell_quantity and sell_quantity > buy_quantity * supply_pressure_ratio:
        signals.append('supply_pressure')

    return {
        'price': price,
        'vwap': vwap,
        'volume': volume,
        'buy_quantity': buy_quantity,
        'sell_quantity': sell_quantity,
        'peak_price': peak_price,
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
    try:
        access_token = config.get_access_token()
    except ValueError as exc:
        print(f'[exit-monitor] {exc}')
        return None
    except Exception as exc:
        print(f'[exit-monitor] Failed to obtain Groww access token: {exc}')
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
    entry_price: float,
    window_minutes: float = DEFAULT_WINDOW_MINUTES,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    trailing_stop_pct: float = DEFAULT_TRAILING_STOP_PCT,
    volume_spike_multiplier: float = DEFAULT_VOLUME_SPIKE_MULTIPLIER,
    supply_pressure_ratio: float = DEFAULT_SUPPLY_PRESSURE_RATIO,
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

    print(f'[exit-monitor] Watching {stock_id} for {window_minutes:.1f} min, entry_price={entry_price}')

    while now_fn() < deadline:
        quote = fetch_quote_fn()
        if quote is None:
            print(f'[exit-monitor] Failed to fetch quote for {stock_id}, skipping this poll')
            sleep_fn(poll_interval_seconds)
            continue

        reading = detect_reversal(
            quote,
            entry_price,
            peak_price,
            previous_price,
            previous_volume,
            trailing_stop_pct=trailing_stop_pct,
            volume_spike_multiplier=volume_spike_multiplier,
            supply_pressure_ratio=supply_pressure_ratio,
        )
        price = reading['price']
        if price is None:
            print(f'[exit-monitor] Quote for {stock_id} had no usable price, skipping this poll')
            sleep_fn(poll_interval_seconds)
            continue

        peak_price = reading['peak_price']
        signals = reading['signals']

        if signals:
            print(
                f"[exit-monitor] ALERT sell {stock_id}: price={price} peak={peak_price} "
                f"signals={', '.join(signals)}"
            )
            append_exit_alert(stock_id, price, entry_price, peak_price, signals, alerts_path)
        else:
            print(f'[exit-monitor] {stock_id} holding: price={price} peak={peak_price}')

        previous_price = price
        previous_volume = reading['volume']

        if now_fn() < deadline:
            sleep_fn(poll_interval_seconds)

    print(f'[exit-monitor] Monitoring window closed for {stock_id}')
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Watch a held intraday position and alert on a momentum reversal')
    parser.add_argument('--stock-id', required=True, help='Trading symbol of the held position')
    parser.add_argument('--entry-price', type=float, required=True, help='Price the position was bought at')
    parser.add_argument('--window-minutes', type=float, default=DEFAULT_WINDOW_MINUTES, help='How long to keep watching')
    parser.add_argument('--poll-interval', type=float, default=DEFAULT_POLL_INTERVAL_SECONDS, help='Seconds between quote polls')
    parser.add_argument('--trailing-stop-pct', type=float, default=DEFAULT_TRAILING_STOP_PCT, help='Percent drop from peak price that triggers an alert')
    parser.add_argument('--volume-spike-multiplier', type=float, default=DEFAULT_VOLUME_SPIKE_MULTIPLIER, help='Volume multiple (vs previous poll) that counts as a down-volume spike')
    parser.add_argument('--supply-pressure-ratio', type=float, default=DEFAULT_SUPPLY_PRESSURE_RATIO, help='Sell-quantity-to-buy-quantity ratio (order book) that counts as supply pressure')
    parser.add_argument('--exit-alerts-file', type=Path, default=EXIT_ALERTS_FILE, help='CSV file to log alerts to')
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run_exit_monitor(
        stock_id=args.stock_id,
        entry_price=args.entry_price,
        window_minutes=args.window_minutes,
        poll_interval_seconds=args.poll_interval,
        trailing_stop_pct=args.trailing_stop_pct,
        volume_spike_multiplier=args.volume_spike_multiplier,
        supply_pressure_ratio=args.supply_pressure_ratio,
        alerts_path=args.exit_alerts_file,
    )


if __name__ == '__main__':
    raise SystemExit(main())
