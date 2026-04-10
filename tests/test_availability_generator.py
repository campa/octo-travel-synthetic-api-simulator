"""Tests for availability generator — slot capping, coherence checks, normalization."""

import pytest

from seeder.availability_generator import (
    _cap_slots,
    _check_coherence,
    _close_day,
    _count_day_slots,
    _fix_coherence,
    _get_option_duration_minutes,
    _normalize_calendar_day,
    _time_to_minutes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _day(status="AVAILABLE", start_times=None, **kw):
    d = {"localDate": "2026-04-10", "status": status, "available": True, **kw}
    if start_times is not None:
        d["availabilityLocalStartTimes"] = start_times
    return d


def _product(avail_type="START_TIME", allow_freesale=False):
    return {"availabilityType": avail_type, "allowFreesale": allow_freesale}


def _option(start_times=None, dur_amount=None, dur_unit=None):
    opt = {"id": "opt-1", "availabilityLocalStartTimes": start_times or []}
    if dur_amount is not None:
        opt["durationAmount"] = dur_amount
    if dur_unit is not None:
        opt["durationUnit"] = dur_unit
    return opt


# ---------------------------------------------------------------------------
# _time_to_minutes
# ---------------------------------------------------------------------------

class TestTimeToMinutes:
    def test_midnight(self):
        assert _time_to_minutes("00:00") == 0

    def test_noon(self):
        assert _time_to_minutes("12:00") == 720

    def test_arbitrary(self):
        assert _time_to_minutes("09:30") == 570


# ---------------------------------------------------------------------------
# _get_option_duration_minutes
# ---------------------------------------------------------------------------

class TestGetOptionDurationMinutes:
    def test_hours(self):
        assert _get_option_duration_minutes({"durationAmount": "2", "durationUnit": "hour"}) == 120

    def test_minutes(self):
        assert _get_option_duration_minutes({"durationAmount": "90", "durationUnit": "minute"}) == 90

    def test_days(self):
        assert _get_option_duration_minutes({"durationAmount": "1", "durationUnit": "day"}) == 1440

    def test_missing(self):
        assert _get_option_duration_minutes({}) is None

    def test_partial(self):
        assert _get_option_duration_minutes({"durationAmount": "2"}) is None


# ---------------------------------------------------------------------------
# _normalize_calendar_day
# ---------------------------------------------------------------------------

class TestNormalizeCalendarDay:
    def test_closed_forces_unavailable(self):
        d = _normalize_calendar_day({"status": "CLOSED", "available": True, "vacancies": 10})
        assert d["available"] is False
        assert d["vacancies"] is None

    def test_sold_out_forces_unavailable(self):
        d = _normalize_calendar_day({"status": "SOLD_OUT", "available": True})
        assert d["available"] is False

    def test_available_forces_available(self):
        d = _normalize_calendar_day({"status": "AVAILABLE", "available": False})
        assert d["available"] is True

    def test_freesale_nulls_vacancies(self):
        d = _normalize_calendar_day({"status": "FREESALE", "vacancies": 5, "capacity": 10})
        assert d["vacancies"] is None
        assert d["capacity"] is None

    def test_defaults_opening_hours(self):
        d = _normalize_calendar_day({"status": "AVAILABLE"})
        assert d["openingHours"] == []


# ---------------------------------------------------------------------------
# _count_day_slots / _close_day
# ---------------------------------------------------------------------------

class TestCountDaySlots:
    def test_start_time_product(self):
        assert _count_day_slots(_day(start_times=["09:00", "11:00", "14:00"])) == 3

    def test_opening_hours_product(self):
        assert _count_day_slots(_day()) == 1

    def test_closed_day(self):
        assert _count_day_slots(_day(status="CLOSED", available=False)) == 0


class TestCloseDay:
    def test_flips_to_closed(self):
        d = _day(start_times=["09:00"])
        _close_day(d)
        assert d["status"] == "CLOSED"
        assert d["available"] is False
        assert d["vacancies"] is None
        assert "availabilityLocalStartTimes" not in d


# ---------------------------------------------------------------------------
# _cap_slots
# ---------------------------------------------------------------------------

class TestCapSlots:
    def test_under_budget_no_change(self):
        days = [_day(start_times=["09:00", "11:00"])]
        result = _cap_slots(days, max_slots=5)
        assert result[0]["status"] == "AVAILABLE"
        assert len(result[0]["availabilityLocalStartTimes"]) == 2

    def test_exact_budget(self):
        days = [_day(start_times=["09:00", "11:00", "14:00"])]
        result = _cap_slots(days, max_slots=3)
        assert len(result[0]["availabilityLocalStartTimes"]) == 3

    def test_trims_start_times(self):
        days = [_day(start_times=["09:00", "11:00", "14:00", "16:00"])]
        result = _cap_slots(days, max_slots=2)
        assert len(result[0]["availabilityLocalStartTimes"]) == 2

    def test_closes_day_when_zero_budget(self):
        days = [_day(start_times=["09:00"])]
        result = _cap_slots(days, max_slots=0)
        assert result[0]["status"] == "CLOSED"

    def test_multi_day_budget_tracking(self):
        days = [
            _day(start_times=["09:00", "11:00"]),
            _day(start_times=["09:00", "11:00", "14:00"]),
            _day(start_times=["09:00"]),
        ]
        result = _cap_slots(days, max_slots=4)
        total = sum(_count_day_slots(d) for d in result)
        assert total <= 4

    def test_opening_hours_caps_by_day(self):
        days = [_day() for _ in range(7)]
        result = _cap_slots(days, max_slots=3)
        open_count = sum(1 for d in result if d["status"] == "AVAILABLE")
        assert open_count == 3

    def test_skips_closed_days(self):
        days = [
            _day(status="CLOSED", available=False),
            _day(start_times=["09:00"]),
        ]
        result = _cap_slots(days, max_slots=1)
        assert result[0]["status"] == "CLOSED"
        assert result[1]["status"] == "AVAILABLE"


# ---------------------------------------------------------------------------
# _check_coherence
# ---------------------------------------------------------------------------

class TestCheckCoherence:
    def test_clean_data_no_issues(self):
        days = [_day(start_times=["09:00", "14:00"])]
        product = _product()
        option = _option(start_times=["09:00", "14:00"])
        assert _check_coherence(days, product, option) == []

    def test_invented_start_time(self):
        days = [_day(start_times=["09:00", "18:00"])]
        product = _product()
        option = _option(start_times=["09:00", "14:00"])
        issues = _check_coherence(days, product, option)
        assert any("18:00" in i for i in issues)

    def test_overlapping_start_times(self):
        days = [_day(start_times=["09:00", "10:00"])]
        product = _product()
        option = _option(start_times=["09:00", "10:00", "14:00"], dur_amount="2", dur_unit="hour")
        issues = _check_coherence(days, product, option)
        assert any("overlap" in i for i in issues)

    def test_freesale_not_allowed(self):
        days = [_day(status="FREESALE")]
        product = _product(allow_freesale=False)
        issues = _check_coherence(days, product, None)
        assert any("FREESALE" in i for i in issues)

    def test_freesale_allowed(self):
        days = [_day(status="FREESALE")]
        product = _product(allow_freesale=True)
        assert _check_coherence(days, product, None) == []

    def test_opening_hours_with_start_times(self):
        days = [_day(start_times=["09:00"])]
        product = _product(avail_type="OPENING_HOURS")
        issues = _check_coherence(days, product, None)
        assert any("OPENING_HOURS" in i for i in issues)


# ---------------------------------------------------------------------------
# _fix_coherence
# ---------------------------------------------------------------------------

class TestFixCoherence:
    def test_filters_to_allowed_subset(self):
        days = [_day(start_times=["09:00", "18:00", "14:00"])]
        product = _product()
        option = _option(start_times=["09:00", "14:00"])
        fixed = _fix_coherence(days, product, option)
        assert fixed[0]["availabilityLocalStartTimes"] == ["09:00", "14:00"]

    def test_removes_overlapping(self):
        days = [_day(start_times=["09:00", "10:00", "14:00"])]
        product = _product()
        option = _option(start_times=["09:00", "10:00", "14:00"], dur_amount="2", dur_unit="hour")
        fixed = _fix_coherence(days, product, option)
        times = fixed[0]["availabilityLocalStartTimes"]
        assert "09:00" in times
        assert "10:00" not in times  # overlaps with 09:00 + 2h
        assert "14:00" in times

    def test_freesale_to_available(self):
        days = [_day(status="FREESALE")]
        product = _product(allow_freesale=False)
        fixed = _fix_coherence(days, product, None)
        assert fixed[0]["status"] == "AVAILABLE"

    def test_strips_start_times_from_opening_hours(self):
        days = [_day(start_times=["09:00"])]
        product = _product(avail_type="OPENING_HOURS")
        fixed = _fix_coherence(days, product, None)
        assert fixed[0].get("availabilityLocalStartTimes") is None

    def test_strips_start_times_from_closed(self):
        days = [{"localDate": "2026-04-10", "status": "CLOSED", "available": False,
                 "availabilityLocalStartTimes": ["09:00"]}]
        product = _product()
        option = _option(start_times=["09:00"])
        fixed = _fix_coherence(days, product, option)
        assert fixed[0].get("availabilityLocalStartTimes") is None
