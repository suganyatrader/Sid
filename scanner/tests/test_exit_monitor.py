import csv
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from exit_monitor import detect_reversal, run_exit_monitor


def test_detect_reversal_flags_when_ratio_drops_below_benchmark():
    quote = {'ltp': 101.0, 'total_buy_quantity': 2000, 'total_sell_quantity': 1000}

    reading = detect_reversal(quote, benchmark_ratio=3.0)

    assert 'demand_vs_supply' in reading['signals']
    assert reading['demand_supply_ratio'] == 2.0
    assert reading['buy_quantity'] == 2000
    assert reading['sell_quantity'] == 1000


def test_detect_reversal_does_not_flag_when_ratio_is_still_above_benchmark():
    quote = {'ltp': 101.0, 'total_buy_quantity': 4000, 'total_sell_quantity': 1000}

    reading = detect_reversal(quote, benchmark_ratio=3.0)

    assert reading['signals'] == []


def _make_fake_clock():
    fake_time = {'now': 0.0}
    return (lambda: fake_time['now']), (lambda seconds: fake_time.update(now=fake_time['now'] + seconds))


def test_run_exit_monitor_alerts_only_after_confirmation_polls(tmp_path):
    quotes = [
        {'ltp': 102.0, 'total_buy_quantity': 3000, 'total_sell_quantity': 1000},
        {'ltp': 96.0, 'total_buy_quantity': 2500, 'total_sell_quantity': 1000},
        {'ltp': 95.0, 'total_buy_quantity': 2000, 'total_sell_quantity': 1000},
        {'ltp': 94.0, 'total_buy_quantity': 1500, 'total_sell_quantity': 1000},
    ]
    quote_iter = iter(quotes)
    fake_now, fake_sleep = _make_fake_clock()
    alerts_file = tmp_path / 'exit_alerts.csv'

    result = run_exit_monitor(
        stock_id='TEST',
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
        assert 'demand_vs_supply' in row['signals']


def test_run_exit_monitor_ignores_single_poll_blip(tmp_path):
    quotes = [
        {'ltp': 102.0, 'total_buy_quantity': 3000, 'total_sell_quantity': 1000},
        {'ltp': 96.0, 'total_buy_quantity': 2500, 'total_sell_quantity': 1000},
        {'ltp': 103.0, 'total_buy_quantity': 4000, 'total_sell_quantity': 1000},
    ]
    quote_iter = iter(quotes)
    fake_now, fake_sleep = _make_fake_clock()
    alerts_file = tmp_path / 'exit_alerts.csv'

    result = run_exit_monitor(
        stock_id='TEST',
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
