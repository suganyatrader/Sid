import csv
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from exit_monitor import detect_reversal, run_exit_monitor


def test_detect_reversal_flags_below_entry_and_vwap():
    quote = {'ltp': 95.0, 'vwap': 98.0, 'volume': 100000}

    reading = detect_reversal(quote, entry_price=100.0, peak_price=100.0, previous_price=None, previous_volume=None)

    assert 'below_entry' in reading['signals']
    assert 'below_vwap' in reading['signals']
    assert reading['peak_price'] == 100.0


def test_detect_reversal_flags_trailing_stop_off_peak():
    quote = {'ltp': 104.0, 'volume': 100000}

    reading = detect_reversal(
        quote, entry_price=100.0, peak_price=110.0, previous_price=None, previous_volume=None,
        trailing_stop_pct=5.0,
    )

    assert 'trailing_stop' in reading['signals']
    assert 'below_entry' not in reading['signals']


def test_detect_reversal_flags_down_volume_spike_against_recent_average():
    # previous poll's cumulative volume was 100000; this poll jumps to 130000 (delta=30000),
    # which is well above the recent average per-poll delta of 5000.
    quote = {'ltp': 99.0, 'volume': 130000}

    reading = detect_reversal(
        quote, entry_price=100.0, peak_price=100.0, previous_price=100.5, previous_volume=100000,
        average_volume_delta=5000, volume_spike_multiplier=1.5,
    )

    assert 'down_volume_spike' in reading['signals']


def test_detect_reversal_does_not_flag_spike_without_delta_history():
    # No average_volume_delta yet (first couple of polls) -> signal can't fire, avoids the
    # old bug where a tiny cumulative-volume denominator near market open caused false spikes.
    quote = {'ltp': 99.0, 'volume': 130000}

    reading = detect_reversal(
        quote, entry_price=100.0, peak_price=100.0, previous_price=100.5, previous_volume=100000,
        average_volume_delta=None,
    )

    assert 'down_volume_spike' not in reading['signals']


def test_detect_reversal_flags_supply_pressure_from_order_book():
    quote = {'ltp': 101.0, 'total_buy_quantity': 1000, 'total_sell_quantity': 3000}

    reading = detect_reversal(
        quote, entry_price=100.0, peak_price=101.0, previous_price=None, previous_volume=None,
        supply_pressure_ratio=1.2,
    )

    assert 'supply_pressure' in reading['signals']
    assert reading['buy_quantity'] == 1000
    assert reading['sell_quantity'] == 3000


def test_detect_reversal_falls_back_to_top_of_book_quantities():
    quote = {'ltp': 101.0, 'bid_quantity': 500, 'offer_quantity': 2000}

    reading = detect_reversal(
        quote, entry_price=100.0, peak_price=101.0, previous_price=None, previous_volume=None,
        supply_pressure_ratio=1.2,
    )

    assert 'supply_pressure' in reading['signals']


def test_detect_reversal_no_signals_while_holding_up():
    quote = {'ltp': 101.0, 'vwap': 100.0, 'volume': 100000}

    reading = detect_reversal(quote, entry_price=100.0, peak_price=101.0, previous_price=100.5, previous_volume=100000)

    assert reading['signals'] == []
    assert reading['peak_price'] == 101.0


def _make_fake_clock():
    fake_time = {'now': 0.0}
    return (lambda: fake_time['now']), (lambda seconds: fake_time.update(now=fake_time['now'] + seconds))


def test_run_exit_monitor_alerts_only_after_confirmation_polls(tmp_path):
    quotes = [
        {'ltp': 102.0, 'vwap': 100.0, 'volume': 100000},  # holding up, sets peak=102
        {'ltp': 96.0, 'vwap': 100.0, 'volume': 100000},   # reversal starts (streak=1, not yet confirmed)
        {'ltp': 95.0, 'vwap': 100.0, 'volume': 100000},   # reversal persists (streak=2 -> confirmed, alert #1)
        {'ltp': 94.0, 'vwap': 100.0, 'volume': 100000},   # reversal persists (streak=3 -> alert #2)
    ]
    quote_iter = iter(quotes)
    fake_now, fake_sleep = _make_fake_clock()
    alerts_file = tmp_path / 'exit_alerts.csv'

    result = run_exit_monitor(
        stock_id='TEST',
        entry_price=100.0,
        window_minutes=2.0,
        poll_interval_seconds=30.0,
        confirmation_polls=2,
        alerts_path=alerts_file,
        fetch_quote_fn=lambda: next(quote_iter, None),
        sleep_fn=fake_sleep,
        now_fn=fake_now,
    )

    assert result == 1
    assert alerts_file.exists()

    with alerts_file.open() as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 2
    for row in rows:
        assert row['stock_id'] == 'TEST'
        assert 'below_entry' in row['signals']
        assert 'below_vwap' in row['signals']


def test_run_exit_monitor_ignores_single_poll_blip(tmp_path):
    quotes = [
        {'ltp': 102.0, 'vwap': 100.0, 'volume': 100000},  # holding up, sets peak=102
        {'ltp': 96.0, 'vwap': 100.0, 'volume': 100000},   # one-off dip (streak=1, unconfirmed)
        {'ltp': 103.0, 'vwap': 100.0, 'volume': 100000},  # recovers, streak resets to 0
    ]
    quote_iter = iter(quotes)
    fake_now, fake_sleep = _make_fake_clock()
    alerts_file = tmp_path / 'exit_alerts.csv'

    result = run_exit_monitor(
        stock_id='TEST',
        entry_price=100.0,
        window_minutes=1.5,
        poll_interval_seconds=30.0,
        confirmation_polls=2,
        alerts_path=alerts_file,
        fetch_quote_fn=lambda: next(quote_iter, None),
        sleep_fn=fake_sleep,
        now_fn=fake_now,
    )

    assert result == 1
    assert not alerts_file.exists()
