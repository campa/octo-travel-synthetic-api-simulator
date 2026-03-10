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
        opt = data["options"][0]
        assert "internalName" in opt
        assert "availabilityLocalStartTimes" in opt
        unit = opt["units"][0]
        assert "internalName" in unit

    def test_json_serializable(self):
        p = _make_product()
        data = p.model_dump(by_alias=True)
        text = json.dumps(data)
        assert isinstance(text, str)


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
