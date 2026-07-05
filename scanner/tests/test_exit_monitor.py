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


def test_detect_reversal_flags_down_volume_spike():
    quote = {'ltp': 99.0, 'volume': 500000}

    reading = detect_reversal(
        quote, entry_price=100.0, peak_price=100.0, previous_price=100.5, previous_volume=100000,
        volume_spike_multiplier=1.5,
    )

    assert 'down_volume_spike' in reading['signals']


def test_detect_reversal_no_signals_while_holding_up():
    quote = {'ltp': 101.0, 'vwap': 100.0, 'volume': 100000}

    reading = detect_reversal(quote, entry_price=100.0, peak_price=101.0, previous_price=100.5, previous_volume=100000)

    assert reading['signals'] == []
    assert reading['peak_price'] == 101.0


def test_run_exit_monitor_alerts_and_logs_reversal(tmp_path):
    quotes = [
        {'ltp': 102.0, 'vwap': 100.0, 'volume': 100000},  # holding up, sets peak
        {'ltp': 96.0, 'vwap': 100.0, 'volume': 100000},   # reversal: below_entry + below_vwap
    ]
    quote_iter = iter(quotes)

    fake_time = {'now': 0.0}

    def fake_now():
        return fake_time['now']

    def fake_sleep(seconds):
        fake_time['now'] += seconds

    alerts_file = tmp_path / 'exit_alerts.csv'

    result = run_exit_monitor(
        stock_id='TEST',
        entry_price=100.0,
        window_minutes=1.0,
        poll_interval_seconds=30.0,
        alerts_path=alerts_file,
        fetch_quote_fn=lambda: next(quote_iter, None),
        sleep_fn=fake_sleep,
        now_fn=fake_now,
    )

    assert result == 1
    assert alerts_file.exists()

    with alerts_file.open() as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 1
    assert rows[0]['stock_id'] == 'TEST'
    assert 'below_entry' in rows[0]['signals']
    assert 'below_vwap' in rows[0]['signals']
