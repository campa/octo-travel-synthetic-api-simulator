"""Smoke tests for OCTO data models — verify construction, serialization, and round-trip."""

import json

import pytest

from models.product import (
    AvailabilityType,
    DeliveryFormat,
    DeliveryMethod,
    FAQ,
    Feature,
    FeatureType,
    IncludedTax,
    Location,
    LocationType,
    Media,
    MediaRel,
    Option,
    PickupLocation,
    Place,
    PricingFrom,
    PricingPer,
    Product,
    RedemptionMethod,
    Unit,
    UnitType,
)
from models.errors import ErrorResponse


def _make_unit(**overrides) -> dict:
    """Helper to build a minimal valid Unit dict (alias keys)."""
    defaults = dict(
        id="unit-1",
        internalName="Adult",
        reference=None,
        type="ADULT",
        requiredContactFields=[],
        restrictions={
            "minAge": 18, "maxAge": 0, "idRequired": False,
            "minQuantity": None, "maxQuantity": None,
            "paxCount": 1, "accompaniedBy": [],
        },
    )
    defaults.update(overrides)
    return defaults


def _make_option(**overrides) -> dict:
    """Helper to build a minimal valid Option dict (alias keys)."""
    defaults = dict(
        id="opt-1",
        default=False,
        internalName="Default",
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
    """Helper to build a minimal valid Product."""
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


class TestProductModel:
    def test_construction(self):
        p = _make_product()
        assert p.id == "aaaa-bbbb-cccc-dddd"
        assert p.internal_name == "General Admission"
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

    def test_opening_hours_empty_start_times(self):
        p = _make_product(
            availabilityType=AvailabilityType.OPENING_HOURS,
            options=[_make_option(availabilityLocalStartTimes=[])],
        )
        assert p.availability_type == AvailabilityType.OPENING_HOURS
        assert p.options[0].availability_local_start_times == []

    def test_all_delivery_formats(self):
        """All delivery formats from the OCTO spec should be valid."""
        all_formats = [
            DeliveryFormat.PDF_URL, DeliveryFormat.QRCODE,
            DeliveryFormat.PNG_URL, DeliveryFormat.PKPASS_URL,
            DeliveryFormat.GOOGLE_WALLET_URL, DeliveryFormat.HTML_URL,
            DeliveryFormat.CODE128, DeliveryFormat.AZTECCODE,
        ]
        p = _make_product(deliveryFormats=all_formats)
        assert len(p.delivery_formats) == 8


class TestP0Validations:
    """P0 — Validation bugs that must be caught."""

    def test_cancellation_cutoff_valid_formats(self):
        for cutoff in ["0 hours", "24 hours", "48 hours", "7 days", "30 minutes", "1 hour", "1 day", "1 minute"]:
            opt = Option(**_make_option(cancellationCutoff=cutoff))
            assert opt.cancellation_cutoff == cutoff

    def test_cancellation_cutoff_rejects_bare_unit(self):
        with pytest.raises(Exception, match="cancellationCutoff"):
            Option(**_make_option(cancellationCutoff="hour"))

    def test_cancellation_cutoff_rejects_invalid(self):
        with pytest.raises(Exception, match="cancellationCutoff"):
            Option(**_make_option(cancellationCutoff="soon"))

    def test_reference_null_is_valid(self):
        p = _make_product(reference=None)
        assert p.reference is None

    def test_reference_short_code_is_valid(self):
        p = _make_product(reference="ABCD")
        assert p.reference == "ABCD"

    def test_reference_rejects_uuid(self):
        with pytest.raises(Exception, match="reference"):
            _make_product(reference="66a33f3e-f02a-499a-b53a-a1f924f508bd")

    def test_unit_reference_rejects_uuid(self):
        with pytest.raises(Exception, match="reference"):
            Unit(**_make_unit(reference="66a33f3e-f02a-499a-b53a-a1f924f508bd"))

    def test_option_reference_rejects_uuid(self):
        with pytest.raises(Exception, match="reference"):
            Option(**_make_option(reference="66a33f3e-f02a-499a-b53a-a1f924f508bd"))


class TestPricingCapability:
    """P2 — OCTO Pricing capability fields."""

    def test_product_pricing_fields(self):
        p = _make_product(
            defaultCurrency="USD",
            availableCurrencies=["USD"],
            pricingPer="UNIT",
            includeTax=True,
        )
        assert p.default_currency == "USD"
        assert p.available_currencies == ["USD"]
        assert p.pricing_per == PricingPer.UNIT
        assert p.include_tax is True

    def test_unit_pricing_from(self):
        pricing = [{"original": 3500, "retail": 3500, "net": 2800,
                     "currency": "USD", "currencyPrecision": 2, "includedTaxes": []}]
        u = Unit(**_make_unit(pricingFrom=pricing))
        assert len(u.pricing_from) == 1
        assert u.pricing_from[0].original == 3500
        assert u.pricing_from[0].net == 2800

    def test_pricing_from_net_null(self):
        pricing = [{"original": 5000, "retail": 4500, "net": None,
                     "currency": "EUR", "currencyPrecision": 2, "includedTaxes": []}]
        u = Unit(**_make_unit(pricingFrom=pricing))
        assert u.pricing_from[0].net is None

    def test_pricing_from_retail_gt_original_rejected(self):
        pricing = [{"original": 3000, "retail": 5000, "net": None,
                     "currency": "USD", "currencyPrecision": 2, "includedTaxes": []}]
        with pytest.raises(Exception, match="retail"):
            Unit(**_make_unit(pricingFrom=pricing))

    def test_pricing_from_net_gt_retail_rejected(self):
        pricing = [{"original": 5000, "retail": 3000, "net": 4000,
                     "currency": "USD", "currencyPrecision": 2, "includedTaxes": []}]
        with pytest.raises(Exception, match="net"):
            Unit(**_make_unit(pricingFrom=pricing))

    def test_pricing_from_with_taxes(self):
        pricing = [{"original": 3500, "retail": 3500, "net": 2800,
                     "currency": "USD", "currencyPrecision": 2,
                     "includedTaxes": [{"name": "VAT", "original": 583, "retail": 583, "net": 467}]}]
        u = Unit(**_make_unit(pricingFrom=pricing))
        assert u.pricing_from[0].included_taxes[0].name == "VAT"

    def test_pricing_fields_optional(self):
        """Pricing fields should be None by default (capability not enabled)."""
        p = _make_product()
        assert p.default_currency is None
        assert p.available_currencies is None
        assert p.pricing_per is None


class TestContentCapability:
    """P3 — OCTO Content capability fields."""

    def test_product_content_fields(self):
        p = _make_product(
            title="City Walking Tour",
            shortDescription="A guided walk through the old town.",
            country="GB",
            categoryLabels=["Walking Tours", "Sightseeing"],
            durationMinutesFrom=60,
            durationMinutesTo=90,
        )
        assert p.title == "City Walking Tour"
        assert p.country == "GB"
        assert p.duration_minutes_from == 60

    def test_product_features(self):
        p = _make_product(
            features=[
                {"shortDescription": "Skip the line access", "type": "INCLUSION"},
                {"shortDescription": "Food and drinks", "type": "EXCLUSION"},
            ]
        )
        assert len(p.features) == 2
        assert p.features[0].type == FeatureType.INCLUSION

    def test_product_faqs(self):
        p = _make_product(
            faqs=[{"question": "Is it wheelchair accessible?", "answer": "Yes."}]
        )
        assert p.faqs[0].question == "Is it wheelchair accessible?"

    def test_product_media(self):
        p = _make_product(
            media=[{"src": "https://example.com/img.jpg", "type": "image/jpeg", "rel": "COVER"}]
        )
        assert p.media[0].rel == MediaRel.COVER

    def test_product_locations(self):
        p = _make_product(
            locations=[{
                "title": "Meeting Point",
                "types": ["START"],
                "place": {"latitude": 51.5074, "longitude": -0.1278},
            }]
        )
        assert p.locations[0].types == [LocationType.START]
        assert p.locations[0].place.latitude == 51.5074

    def test_unit_content_fields(self):
        u = Unit(**_make_unit(
            title="Adult",
            titlePlural="Adults",
            subtitle="18 - 65 Years",
        ))
        assert u.title == "Adult"
        assert u.title_plural == "Adults"
        assert u.subtitle == "18 - 65 Years"

    def test_option_content_fields(self):
        opt = Option(**_make_option(
            title="Standard Tour",
            shortDescription="The classic experience.",
            duration="2 hours",
            durationAmount="2",
            durationUnit="hour",
        ))
        assert opt.title == "Standard Tour"
        assert opt.duration == "2 hours"

    def test_content_fields_optional(self):
        """Content fields should be None by default (capability not enabled)."""
        p = _make_product()
        assert p.title is None
        assert p.short_description is None
        assert p.features is None
        assert p.country is None


class TestPickupsCapability:
    """P4 — OCTO Pickups capability fields."""

    def test_option_pickup_fields(self):
        opt = Option(**_make_option(
            pickupAvailable=True,
            pickupRequired=False,
            pickupLocations=[{
                "id": "loc-1",
                "title": "Hotel Lobby",
                "shortDescription": "Main entrance",
                "place": {"latitude": 40.7128, "longitude": -74.0060,
                          "postalAddress": "123 Main St"},
            }],
        ))
        assert opt.pickup_available is True
        assert opt.pickup_required is False
        assert len(opt.pickup_locations) == 1
        assert opt.pickup_locations[0].title == "Hotel Lobby"

    def test_pickup_fields_optional(self):
        """Pickup fields should be None by default."""
        opt = Option(**_make_option())
        assert opt.pickup_available is None
        assert opt.pickup_locations is None


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
