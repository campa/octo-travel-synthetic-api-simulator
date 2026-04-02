"""Validate that Pydantic Product output conforms to the OCTO OpenAPI spec.

Loads the product JSON Schema from api-spec/octo-spec.yaml and validates
serialized Product instances against it.  This catches drift between the
Pydantic model and the spec (missing fields, wrong types, extra required
fields, enum mismatches, etc.).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError

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

# ---------------------------------------------------------------------------
# Schema extraction
# ---------------------------------------------------------------------------

SPEC_PATH = Path(__file__).resolve().parent.parent / "api-spec" / "octo-spec.yaml"


def _load_product_schema() -> dict:
    """Extract the product item JSON Schema from the OpenAPI spec."""
    with open(SPEC_PATH) as f:
        spec = yaml.safe_load(f)

    # /products → GET → 200 → application/json → schema → items
    products_response = (
        spec["paths"]["/products"]["get"]["responses"]["200"]["content"]
        ["application/json"]["schema"]
    )
    assert products_response["type"] == "array", "Expected array schema for /products"
    product_schema = products_response["items"]

    # OpenAPI 3.1 uses JSON Schema 2020-12 compatible types.
    # The spec uses nullable via type arrays like ["null", "string"].
    # jsonschema handles this natively with Draft202012Validator.
    return product_schema


PRODUCT_SCHEMA = _load_product_schema()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_product(data: dict) -> list[str]:
    """Validate a product dict against the OCTO spec schema.

    Returns a list of error messages (empty = valid).
    """
    validator = Draft202012Validator(PRODUCT_SCHEMA)
    return [e.message for e in validator.iter_errors(data)]


def _make_unit(**overrides) -> dict:
    defaults = dict(
        id="unit-1",
        internalName="Adult",
        reference=None,
        type="ADULT",
        requiredContactFields=[],
        restrictions={
            "minAge": 18, "maxAge": 65, "idRequired": False,
            "minQuantity": 1, "maxQuantity": None,
            "paxCount": 1, "accompaniedBy": [],
        },
    )
    defaults.update(overrides)
    return defaults


def _make_option(**overrides) -> dict:
    defaults = dict(
        id="opt-1",
        default=True,
        internalName="Standard",
        reference=None,
        availabilityLocalStartTimes=["09:00"],
        cancellationCutoff="0 hours",
        cancellationCutoffAmount=0,
        cancellationCutoffUnit="hour",
        requiredContactFields=[],
        restrictions={"minUnits": 0, "maxUnits": None},
        units=[_make_unit()],
    )
    defaults.update(overrides)
    return defaults


def _make_product(**overrides) -> Product:
    defaults = dict(
        id="aaaa-bbbb-cccc-dddd",
        internalName="General Admission",
        reference=None,
        locale="en",
        timeZone="Europe/London",
        allowFreesale=False,
        instantConfirmation=True,
        instantDelivery=True,
        availabilityRequired=True,
        availabilityType=AvailabilityType.START_TIME,
        deliveryFormats=[DeliveryFormat.QRCODE],
        deliveryMethods=[DeliveryMethod.TICKET],
        redemptionMethod=RedemptionMethod.DIGITAL,
        options=[_make_option()],
    )
    defaults.update(overrides)
    return Product(**defaults)


def _serialize(product: Product) -> dict:
    """Serialize a Product the same way the server does."""
    return product.model_dump(by_alias=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSpecCompliance:
    """Validate serialized Product JSON against the OCTO OpenAPI spec."""

    def test_minimal_product_conforms(self):
        """A minimal product (no capabilities) must pass spec validation."""
        data = _serialize(_make_product())
        errors = _validate_product(data)
        assert errors == [], f"Spec validation errors:\n" + "\n".join(errors)

    def test_product_with_pricing_conforms(self):
        """Product with pricing capability fields must still conform."""
        data = _serialize(_make_product(
            defaultCurrency="EUR",
            availableCurrencies=["EUR", "USD"],
            pricingPer="UNIT",
            includeTax=True,
        ))
        errors = _validate_product(data)
        assert errors == [], f"Spec validation errors:\n" + "\n".join(errors)

    def test_product_with_content_conforms(self):
        """Product with content capability fields must still conform."""
        data = _serialize(_make_product(
            title="City Walking Tour",
            shortDescription="A guided walk.",
            country="GB",
            categoryLabels=["Walking Tours"],
            durationMinutesFrom=60,
            durationMinutesTo=90,
            features=[{"shortDescription": "Skip the line", "type": "INCLUSION"}],
            faqs=[{"question": "Wheelchair?", "answer": "Yes"}],
            media=[{"src": "https://example.com/img.jpg", "type": "image/jpeg", "rel": "COVER"}],
            locations=[{
                "title": "Start",
                "types": ["START"],
                "place": {"latitude": 51.5, "longitude": -0.1},
            }],
        ))
        errors = _validate_product(data)
        assert errors == [], f"Spec validation errors:\n" + "\n".join(errors)

    def test_product_with_pickups_conforms(self):
        """Product with pickup capability fields must still conform."""
        data = _serialize(_make_product(
            options=[_make_option(
                pickupAvailable=True,
                pickupRequired=False,
                pickupLocations=[{
                    "id": "loc-1",
                    "title": "Hotel Lobby",
                    "shortDescription": "Main entrance",
                    "place": {"latitude": 40.7, "longitude": -74.0},
                }],
            )],
        ))
        errors = _validate_product(data)
        assert errors == [], f"Spec validation errors:\n" + "\n".join(errors)

    def test_fully_loaded_product_conforms(self):
        """Product with all capabilities enabled must conform."""
        data = _serialize(_make_product(
            defaultCurrency="USD",
            availableCurrencies=["USD"],
            pricingPer="UNIT",
            includeTax=False,
            title="Full Tour",
            shortDescription="Everything included.",
            country="US",
            features=[{"shortDescription": "Guide", "type": "INCLUSION"}],
            options=[_make_option(
                title="VIP",
                shortDescription="Premium experience",
                duration="2 hours",
                durationAmount="2",
                durationUnit="hour",
                pickupAvailable=True,
                pickupRequired=True,
                pickupLocations=[{
                    "id": "pk-1",
                    "title": "Lobby",
                    "shortDescription": "Hotel lobby",
                    "place": {"latitude": 34.0, "longitude": -118.2},
                }],
                units=[_make_unit(
                    title="Adult",
                    titlePlural="Adults",
                    subtitle="18+",
                    pricingFrom=[{
                        "original": 5000, "retail": 5000, "net": 4000,
                        "currency": "USD", "currencyPrecision": 2,
                        "includedTaxes": [],
                    }],
                )],
            )],
        ))
        errors = _validate_product(data)
        assert errors == [], f"Spec validation errors:\n" + "\n".join(errors)

    def test_all_required_fields_present(self):
        """Verify the serialized output includes every field the spec marks required."""
        spec_required = PRODUCT_SCHEMA.get("required", [])
        data = _serialize(_make_product())
        missing = [f for f in spec_required if f not in data]
        assert missing == [], f"Missing required fields: {missing}"

    def test_option_required_fields_present(self):
        """Verify option output includes every field the spec marks required."""
        option_schema = PRODUCT_SCHEMA["properties"]["options"]["items"]
        spec_required = option_schema.get("required", [])
        data = _serialize(_make_product())
        option_data = data["options"][0]
        missing = [f for f in spec_required if f not in option_data]
        assert missing == [], f"Missing required option fields: {missing}"

    def test_unit_required_fields_present(self):
        """Verify unit output includes every field the spec marks required."""
        unit_schema = (
            PRODUCT_SCHEMA["properties"]["options"]["items"]
            ["properties"]["units"]["items"]
        )
        spec_required = unit_schema.get("required", [])
        data = _serialize(_make_product())
        unit_data = data["options"][0]["units"][0]
        missing = [f for f in spec_required if f not in unit_data]
        assert missing == [], f"Missing required unit fields: {missing}"

    def test_json_is_serializable(self):
        """Sanity check: the output is valid JSON (no datetime objects etc.)."""
        data = _serialize(_make_product())
        text = json.dumps(data)
        assert isinstance(text, str)
