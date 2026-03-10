"""Smoke tests for OCTO data models — verify construction, serialization, and round-trip."""

import json

from models.product import (
    AvailabilityType,
    DeliveryFormat,
    DeliveryMethod,
    Option,
    Product,
    RedemptionMethod,
    Unit,
    UnitType,
)
from models.availability import (
    AvailabilitySlot,
    AvailabilityStatus,
    CalendarEntry,
    OpeningHours,
)
from models.errors import ErrorResponse


def _make_product(**overrides) -> Product:
    """Helper to build a minimal valid Product."""
    defaults = dict(
        id="aaaa-bbbb-cccc-dddd",
        internalName="Test Tour",
        locale="en",
        timeZone="Europe/London",
        availabilityType=AvailabilityType.START_TIME,
        deliveryFormats=[DeliveryFormat.QRCODE],
        deliveryMethods=[DeliveryMethod.TICKET],
        redemptionMethod=RedemptionMethod.DIGITAL,
        options=[
            Option(
                id="opt-1",
                internal_name="Default",
                units=[Unit(id="unit-1", internal_name="Adult", type=UnitType.ADULT)],
            )
        ],
    )
    defaults.update(overrides)
    return Product(**defaults)


class TestProductModel:
    def test_construction(self):
        p = _make_product()
        assert p.id == "aaaa-bbbb-cccc-dddd"
        assert p.internal_name == "Test Tour"
        assert len(p.options) == 1
        assert len(p.options[0].units) == 1

    def test_json_round_trip(self):
        original = _make_product()
        data = original.model_dump(by_alias=True)
        restored = Product(**data)
        assert restored == original

    def test_camel_case_serialization(self):
        p = _make_product()
        data = p.model_dump(by_alias=True)
        assert "internalName" in data
        assert "availabilityType" in data
        assert "deliveryFormats" in data
        assert "timeZone" in data
        # Nested option
        opt = data["options"][0]
        assert "internalName" in opt
        assert "availabilityLocalStartTimes" in opt
        # Nested unit
        unit = opt["units"][0]
        assert "internalName" in unit

    def test_json_serializable(self):
        p = _make_product()
        data = p.model_dump(by_alias=True)
        text = json.dumps(data)
        assert isinstance(text, str)


class TestAvailabilityModels:
    def test_calendar_entry_round_trip(self):
        entry = CalendarEntry(
            local_date="2026-03-01",
            available=True,
            status=AvailabilityStatus.AVAILABLE,
            vacancies=50,
            capacity=100,
        )
        data = entry.model_dump(by_alias=True)
        restored = CalendarEntry(**data)
        assert restored == entry
        assert data["localDate"] == "2026-03-01"

    def test_availability_slot_round_trip(self):
        slot = AvailabilitySlot(
            id="slot-1",
            local_date_time_start="2026-03-01T09:00:00",
            local_date_time_end="2026-03-01T10:00:00",
            all_day=False,
            available=True,
            status=AvailabilityStatus.AVAILABLE,
            vacancies=20,
            capacity=40,
            utc_cutoff_at="2026-03-01T09:00:00Z",
        )
        data = slot.model_dump(by_alias=True)
        restored = AvailabilitySlot(**data)
        assert restored == slot
        assert data["localDateTimeStart"] == "2026-03-01T09:00:00"
        assert data["allDay"] is False

    def test_opening_hours_alias(self):
        oh = OpeningHours(from_time="09:00", to_time="17:00")
        data = oh.model_dump(by_alias=True)
        assert data["from"] == "09:00"
        assert data["to"] == "17:00"


class TestErrorResponse:
    def test_construction_and_alias(self):
        err = ErrorResponse(
            error="INVALID_PRODUCT_ID",
            error_message="The productId was missing or invalid",
            error_id="err-uuid-1234",
        )
        data = err.model_dump(by_alias=True)
        assert data["error"] == "INVALID_PRODUCT_ID"
        assert data["errorMessage"] == "The productId was missing or invalid"
        assert data["errorId"] == "err-uuid-1234"
