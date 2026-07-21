import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from nse_postclose_scraper import FilingSource, parse_date, parse_datetime, should_include_record, with_retry


def _source(timestamp_fields, date_fields):
    return FilingSource(
        name="test",
        url="http://example.test",
        referer="http://example.test/ref",
        symbol_fields=("symbol",),
        timestamp_fields=timestamp_fields,
        date_fields=date_fields,
        attachment_fields=(),
    )


def test_parse_datetime_handles_uppercase_month():
    parsed = parse_datetime("06-JUL-2026 16:56:31")
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.month == 7
    assert parsed.day == 6
    assert parsed.hour == 16
    assert parsed.minute == 56


def test_parse_date_handles_compact_date():
    parsed = parse_date("31-Dec-2025")
    assert parsed == date(2025, 12, 31)


def test_should_include_record_after_cutoff_time():
    source = _source(("exchdisstime",), ("exDate",))
    record = {"exchdisstime": "09-Jul-2026 15:45:00"}
    assert should_include_record(record, source, target_date=date(2026, 7, 9))


def test_should_exclude_record_before_cutoff_time():
    source = _source(("exchdisstime",), ("exDate",))
    record = {"exchdisstime": "09-Jul-2026 15:10:00"}
    assert not should_include_record(record, source, target_date=date(2026, 7, 9))


def test_should_include_previous_day_when_time_missing():
    source = _source(("caBroadcastDate",), ("exDate", "recDate"))
    record = {"caBroadcastDate": None, "exDate": "08-Jul-2026"}
    assert should_include_record(record, source, target_date=date(2026, 7, 9))


def test_with_retry_retries_and_succeeds():
    state = {"attempts": 0, "sleeps": []}

    def flaky():
        state["attempts"] += 1
        if state["attempts"] < 3:
            raise RuntimeError("temporary failure")
        return "ok"

    result = with_retry(flaky, max_attempts=3, base_delay_seconds=0.25, sleep_fn=state["sleeps"].append)
    assert result == "ok"
    assert state["sleeps"] == [0.25, 0.5]


def test_with_retry_raises_after_max_attempts():
    state = {"attempts": 0}

    def always_fails():
        state["attempts"] += 1
        raise ValueError("permanent failure")

    with pytest.raises(ValueError):
        with_retry(always_fails, max_attempts=3, base_delay_seconds=0.1, sleep_fn=lambda _: None)
    assert state["attempts"] == 3
