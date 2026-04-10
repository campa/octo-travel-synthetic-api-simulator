"""Tests for OCTO Availability Calendar models — construction, validation, serialization."""

import json

import pytest

from models.availability import (
    AvailabilityCalendarDay,
    AvailabilityStatus,
    OpeningHours,
)


def _make_day(**overrides) -> dict:
    """Helper to build a minimal valid calendar day dict (alias keys)."""
    defaults = dict(
        localDate="2026-04-10",
        available=True,
        status="AVAILABLE",
        vacancies=25,
        capacity=50,
        openingHours=[],
    )
    defaults.update(overrides)
    return defaults


class TestOpeningHours:
    def test_valid(self):
        oh = OpeningHours.model_validate({"from": "09:00", "to": "17:00"})
        assert oh.from_time == "09:00"
        assert oh.to_time == "17:00"

    def test_with_frequency(self):
        oh = OpeningHours.model_validate({
            "from": "08:00", "to": "20:00",
            "frequency": "Every 30 minutes",
            "frequencyAmount": 30,
            "frequencyUnit": "minute",
        })
        assert oh.frequency_amount == 30

    def test_invalid_time_format(self):
        with pytest.raises(Exception, match="HH:MM"):
            OpeningHours.model_validate({"from": "9am", "to": "17:00"})

    def test_serialization_aliases(self):
        oh = OpeningHours.model_validate({"from": "09:00", "to": "17:00"})
        data = oh.model_dump(by_alias=True)
        assert "from" in data
        assert "to" in data


class TestAvailabilityCalendarDay:
    def test_construction(self):
        day = AvailabilityCalendarDay.model_validate(_make_day())
        assert day.local_date == "2026-04-10"
        assert day.available is True
        assert day.status == AvailabilityStatus.AVAILABLE

    def test_json_round_trip(self):
        original = AvailabilityCalendarDay.model_validate(_make_day())
        data = original.model_dump(by_alias=True)
        restored = AvailabilityCalendarDay.model_validate(data)
        assert restored == original

    def test_camel_case_serialization(self):
        day = AvailabilityCalendarDay.model_validate(_make_day())
        data = day.model_dump(by_alias=True)
        assert "localDate" in data
        assert "openingHours" in data
        assert "statusMessage" in data

    def test_json_serializable(self):
        day = AvailabilityCalendarDay.model_validate(_make_day())
        text = json.dumps(day.model_dump(by_alias=True))
        assert isinstance(text, str)

    def test_with_opening_hours(self):
        day = AvailabilityCalendarDay.model_validate(_make_day(
            openingHours=[{"from": "09:00", "to": "17:00"}],
        ))
        assert len(day.opening_hours) == 1
        assert day.opening_hours[0].from_time == "09:00"

    def test_with_start_times(self):
        day = AvailabilityCalendarDay.model_validate(_make_day(
            availabilityLocalStartTimes=["09:00", "11:00", "14:00"],
        ))
        assert day.availability_local_start_times == ["09:00", "11:00", "14:00"]

    def test_extended_fields(self):
        day = AvailabilityCalendarDay.model_validate(_make_day(
            maxUnits=6,
            totalCapacity=100,
            statusMessage="Available",
        ))
        assert day.max_units == 6
        assert day.total_capacity == 100
        assert day.status_message == "Available"

    def test_extended_fields_optional(self):
        day = AvailabilityCalendarDay.model_validate(_make_day())
        assert day.max_units is None
        assert day.total_capacity is None
        assert day.availability_local_start_times is None


class TestStatusConsistency:
    def test_closed_must_be_unavailable(self):
        with pytest.raises(Exception, match="CLOSED"):
            AvailabilityCalendarDay.model_validate(
                _make_day(status="CLOSED", available=True)
            )

    def test_sold_out_must_be_unavailable(self):
        with pytest.raises(Exception, match="SOLD_OUT"):
            AvailabilityCalendarDay.model_validate(
                _make_day(status="SOLD_OUT", available=True)
            )

    def test_freesale_must_not_have_vacancies(self):
        with pytest.raises(Exception, match="FREESALE"):
            AvailabilityCalendarDay.model_validate(
                _make_day(status="FREESALE", available=True, vacancies=10)
            )

    def test_limited_must_be_available(self):
        with pytest.raises(Exception, match="LIMITED"):
            AvailabilityCalendarDay.model_validate(
                _make_day(status="LIMITED", available=False)
            )

    def test_available_must_be_available(self):
        with pytest.raises(Exception, match="AVAILABLE"):
            AvailabilityCalendarDay.model_validate(
                _make_day(status="AVAILABLE", available=False)
            )

    def test_valid_closed(self):
        day = AvailabilityCalendarDay.model_validate(
            _make_day(status="CLOSED", available=False, vacancies=None, capacity=None)
        )
        assert day.status == AvailabilityStatus.CLOSED

    def test_valid_sold_out(self):
        day = AvailabilityCalendarDay.model_validate(
            _make_day(status="SOLD_OUT", available=False, vacancies=0, capacity=50)
        )
        assert day.status == AvailabilityStatus.SOLD_OUT

    def test_valid_freesale(self):
        day = AvailabilityCalendarDay.model_validate(
            _make_day(status="FREESALE", available=True, vacancies=None, capacity=None)
        )
        assert day.status == AvailabilityStatus.FREESALE

    def test_valid_limited(self):
        day = AvailabilityCalendarDay.model_validate(
            _make_day(status="LIMITED", available=True, vacancies=10, capacity=50)
        )
        assert day.status == AvailabilityStatus.LIMITED


class TestDateValidation:
    def test_valid_date(self):
        day = AvailabilityCalendarDay.model_validate(_make_day(localDate="2026-12-31"))
        assert day.local_date == "2026-12-31"

    def test_invalid_date_format(self):
        with pytest.raises(Exception, match="YYYY-MM-DD"):
            AvailabilityCalendarDay.model_validate(_make_day(localDate="31/12/2026"))

    def test_invalid_date_value(self):
        with pytest.raises(Exception):
            AvailabilityCalendarDay.model_validate(_make_day(localDate="2026-13-01"))
