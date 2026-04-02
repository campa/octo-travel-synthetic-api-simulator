"""OCTO Product, Option, Unit Pydantic models and related enums.

Uses strict Python types where possible:
- ``zoneinfo.ZoneInfo`` for IANA timezone validation
- ``langcodes.tag_is_valid`` for BCP 47 locale validation
- ``datetime.time`` for HH:MM start-time validation
- ``pydantic.NonNegativeInt`` / ``PositiveInt`` for numeric constraints

Includes OCTO capability models:
- Pricing (octo/pricing): defaultCurrency, availableCurrencies, pricingPer, includeTax, pricingFrom
- Content (octo/content): title, description, features, faqs, media, locations at product/option/unit levels
- Pickups (octo/pickups): pickupAvailable, pickupRequired, pickupLocations on options
"""

import datetime
import re
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import langcodes
from pydantic import (  # noqa: I001
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    field_serializer,
    field_validator,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AvailabilityType(str, Enum):
    START_TIME = "START_TIME"
    OPENING_HOURS = "OPENING_HOURS"


class DeliveryFormat(str, Enum):
    PDF_URL = "PDF_URL"
    QRCODE = "QRCODE"
    PNG_URL = "PNG_URL"
    PKPASS_URL = "PKPASS_URL"
    GOOGLE_WALLET_URL = "GOOGLE_WALLET_URL"
    HTML_URL = "HTML_URL"
    CODE128 = "CODE128"
    AZTECCODE = "AZTECCODE"


class DeliveryMethod(str, Enum):
    VOUCHER = "VOUCHER"
    TICKET = "TICKET"


class RedemptionMethod(str, Enum):
    DIGITAL = "DIGITAL"
    PRINT = "PRINT"
    MANIFEST = "MANIFEST"


class UnitType(str, Enum):
    ADULT = "ADULT"
    YOUTH = "YOUTH"
    CHILD = "CHILD"
    INFANT = "INFANT"
    FAMILY = "FAMILY"
    SENIOR = "SENIOR"
    STUDENT = "STUDENT"
    MILITARY = "MILITARY"
    OTHER = "OTHER"


class CancellationCutoffUnit(str, Enum):
    HOUR = "hour"
    MINUTE = "minute"
    DAY = "day"


class ContactField(str, Enum):
    FIRST_NAME = "firstName"
    LAST_NAME = "lastName"
    EMAIL_ADDRESS = "emailAddress"
    PHONE_NUMBER = "phoneNumber"
    COUNTRY = "country"
    NOTES = "notes"
    LOCALES = "locales"


class PricingPer(str, Enum):
    UNIT = "UNIT"
    BOOKING = "BOOKING"


class FeatureType(str, Enum):
    INCLUSION = "INCLUSION"
    EXCLUSION = "EXCLUSION"
    HIGHLIGHT = "HIGHLIGHT"
    PREBOOKING_INFORMATION = "PREBOOKING_INFORMATION"
    PREARRIVAL_INFORMATION = "PREARRIVAL_INFORMATION"
    REDEMPTION_INSTRUCTION = "REDEMPTION_INSTRUCTION"
    ACCESSIBILITY_INFORMATION = "ACCESSIBILITY_INFORMATION"
    ADDITIONAL_INFORMATION = "ADDITIONAL_INFORMATION"
    BOOKING_TERM = "BOOKING_TERM"
    CANCELLATION_TERM = "CANCELLATION_TERM"


class MediaRel(str, Enum):
    LOGO = "LOGO"
    COVER = "COVER"
    GALLERY = "GALLERY"


class LocationType(str, Enum):
    START = "START"
    END = "END"
    ITINERARY_ITEM = "ITINERARY_ITEM"
    POINT_OF_INTEREST = "POINT_OF_INTEREST"
    ADMISSION_INCLUDED = "ADMISSION_INCLUDED"
    REDEMPTION = "REDEMPTION"


class DurationUnit(str, Enum):
    HOUR = "hour"
    MINUTE = "minute"
    DAY = "day"


# ---------------------------------------------------------------------------
# Regex for cancellationCutoff validation (P0)
# ---------------------------------------------------------------------------
_CANCELLATION_CUTOFF_RE = re.compile(r"^\d+\s+(hours?|minutes?|days?)$")

# Regex for reference validation (P0) — null or short alphanumeric code
_REFERENCE_RE = re.compile(r"^[A-Za-z0-9_\-]{1,20}$")


# ---------------------------------------------------------------------------
# OCTO Pricing capability models (P2)
# ---------------------------------------------------------------------------

class IncludedTax(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    original: int
    retail: int
    net: Optional[int] = None


class PricingFrom(BaseModel):
    """Unit-level pricing from the octo/pricing capability."""
    model_config = ConfigDict(populate_by_name=True)
    original: int
    retail: int
    net: Optional[int] = None
    currency: str
    currency_precision: int = Field(alias="currencyPrecision", default=2)
    included_taxes: list[IncludedTax] = Field(alias="includedTaxes", default_factory=list)

    @field_validator("retail")
    @classmethod
    def _retail_le_original(cls, v: int, info) -> int:
        original = info.data.get("original")
        if original is not None and v > original:
            raise ValueError(f"retail ({v}) must be <= original ({original})")
        return v

    @field_validator("net")
    @classmethod
    def _net_le_retail(cls, v: Optional[int], info) -> Optional[int]:
        if v is None:
            return v
        retail = info.data.get("retail")
        if retail is not None and v > retail:
            raise ValueError(f"net ({v}) must be <= retail ({retail})")
        return v


# ---------------------------------------------------------------------------
# OCTO Content capability models (P3)
# ---------------------------------------------------------------------------

class Feature(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    short_description: str = Field(alias="shortDescription")
    type: FeatureType


class FAQ(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    question: str
    answer: str


class Media(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    src: str
    type: str  # MIME type e.g. "image/jpeg"
    rel: MediaRel
    title: Optional[str] = None
    caption: Optional[str] = None
    copyright: Optional[str] = None


class Place(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    postal_address: Optional[str] = Field(alias="postalAddress", default=None)
    identifiers: Optional[dict[str, str]] = None
    same_as: Optional[list[str]] = Field(alias="sameAs", default=None)


class Location(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    title: Optional[str] = None
    short_description: Optional[str] = Field(alias="shortDescription", default=None)
    types: list[LocationType] = Field(default_factory=list)
    place: Optional[Place] = None


class Commentary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    format: str  # e.g. "AUDIO_GUIDE"
    language: str  # BCP 47 tag


# ---------------------------------------------------------------------------
# OCTO Pickups capability models (P4)
# ---------------------------------------------------------------------------

class PickupLocation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    title: str
    short_description: Optional[str] = Field(alias="shortDescription", default=None)
    place: Optional[Place] = None


# ---------------------------------------------------------------------------
# Core models (Unit, Option, Product) with capability fields
# ---------------------------------------------------------------------------

class UnitRestrictions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    min_age: NonNegativeInt = Field(alias="minAge")
    max_age: NonNegativeInt = Field(alias="maxAge")
    id_required: bool = Field(alias="idRequired")
    min_quantity: Optional[NonNegativeInt] = Field(alias="minQuantity")
    max_quantity: Optional[NonNegativeInt] = Field(alias="maxQuantity")
    pax_count: PositiveInt = Field(alias="paxCount")
    accompanied_by: list[str] = Field(alias="accompaniedBy", default_factory=list)


class Unit(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    internal_name: str = Field(alias="internalName")
    reference: Optional[str] = None
    type: UnitType
    required_contact_fields: list[ContactField] = Field(
        alias="requiredContactFields",
    )
    restrictions: UnitRestrictions
    # --- octo/pricing ---
    pricing_from: Optional[list[PricingFrom]] = Field(alias="pricingFrom", default=None)
    # --- octo/content ---
    title: Optional[str] = None
    title_plural: Optional[str] = Field(alias="titlePlural", default=None)
    subtitle: Optional[str] = None
    short_description: Optional[str] = Field(alias="shortDescription", default=None)
    features: Optional[list[Feature]] = None

    @field_validator("reference")
    @classmethod
    def _validate_reference(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _REFERENCE_RE.match(v):
            raise ValueError(
                f"reference must be null or a short alphanumeric code (≤20 chars), got {v!r}"
            )
        return v


class OptionRestrictions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    min_units: Optional[NonNegativeInt] = Field(alias="minUnits")
    max_units: Optional[NonNegativeInt] = Field(alias="maxUnits")


class Option(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    default: bool
    internal_name: str = Field(alias="internalName")
    reference: Optional[str] = None
    availability_local_start_times: list[datetime.time] = Field(
        alias="availabilityLocalStartTimes",
    )
    cancellation_cutoff: str = Field(alias="cancellationCutoff")
    cancellation_cutoff_amount: NonNegativeInt = Field(
        alias="cancellationCutoffAmount",
        default=0,
    )
    cancellation_cutoff_unit: CancellationCutoffUnit = Field(
        alias="cancellationCutoffUnit",
    )
    required_contact_fields: list[ContactField] = Field(
        alias="requiredContactFields",
    )
    restrictions: OptionRestrictions
    units: list[Unit]
    # --- octo/content ---
    title: Optional[str] = None
    short_description: Optional[str] = Field(alias="shortDescription", default=None)
    description: Optional[str] = None
    features: Optional[list[Feature]] = None
    faqs: Optional[list[FAQ]] = None
    media: Optional[list[Media]] = None
    duration: Optional[str] = None
    duration_amount: Optional[str] = Field(alias="durationAmount", default=None)
    duration_unit: Optional[DurationUnit] = Field(alias="durationUnit", default=None)
    # --- octo/pickups ---
    pickup_available: Optional[bool] = Field(alias="pickupAvailable", default=None)
    pickup_required: Optional[bool] = Field(alias="pickupRequired", default=None)
    pickup_locations: Optional[list[PickupLocation]] = Field(alias="pickupLocations", default=None)

    @field_validator("availability_local_start_times", mode="before")
    @classmethod
    def _parse_start_times(cls, v: list) -> list[datetime.time]:
        """Accept HH:MM strings and convert to datetime.time objects."""
        result = []
        for item in v:
            if isinstance(item, str):
                try:
                    result.append(datetime.time.fromisoformat(item))
                except ValueError as exc:
                    raise ValueError(f"Invalid time format: {item!r}, expected HH:MM") from exc
            elif isinstance(item, datetime.time):
                result.append(item)
            else:
                raise ValueError(f"Expected time string or datetime.time, got {type(item)}")
        return result

    @field_serializer("availability_local_start_times")
    def _serialize_start_times(self, times: list[datetime.time], _info) -> list[str]:
        """Serialize datetime.time objects back to HH:MM strings."""
        return [t.strftime("%H:%M") for t in times]

    @field_validator("cancellation_cutoff")
    @classmethod
    def _validate_cancellation_cutoff(cls, v: str) -> str:
        """Validate cancellationCutoff matches '{amount} {unit}(s)' pattern."""
        if not _CANCELLATION_CUTOFF_RE.match(v):
            raise ValueError(
                f"cancellationCutoff must match '{{amount}} {{unit}}(s)' "
                f"(e.g. '24 hours', '0 hours', '7 days'), got {v!r}"
            )
        return v

    @field_validator("reference")
    @classmethod
    def _validate_reference(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _REFERENCE_RE.match(v):
            raise ValueError(
                f"reference must be null or a short alphanumeric code (≤20 chars), got {v!r}"
            )
        return v


class Product(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    internal_name: str = Field(alias="internalName")
    reference: Optional[str] = None
    locale: str
    time_zone: str = Field(alias="timeZone")
    allow_freesale: bool = Field(alias="allowFreesale")
    instant_confirmation: bool = Field(alias="instantConfirmation")
    instant_delivery: bool = Field(alias="instantDelivery")
    availability_required: bool = Field(alias="availabilityRequired")
    availability_type: AvailabilityType = Field(alias="availabilityType")
    delivery_formats: list[DeliveryFormat] = Field(alias="deliveryFormats")
    delivery_methods: list[DeliveryMethod] = Field(alias="deliveryMethods")
    redemption_method: RedemptionMethod = Field(alias="redemptionMethod")
    options: list[Option]
    # --- octo/pricing ---
    default_currency: Optional[str] = Field(alias="defaultCurrency", default=None)
    available_currencies: Optional[list[str]] = Field(alias="availableCurrencies", default=None)
    pricing_per: Optional[PricingPer] = Field(alias="pricingPer", default=None)
    include_tax: Optional[bool] = Field(alias="includeTax", default=None)
    # --- octo/content ---
    title: Optional[str] = None
    short_description: Optional[str] = Field(alias="shortDescription", default=None)
    description: Optional[str] = None
    features: Optional[list[Feature]] = None
    faqs: Optional[list[FAQ]] = None
    media: Optional[list[Media]] = None
    locations: Optional[list[Location]] = None
    category_labels: Optional[list[str]] = Field(alias="categoryLabels", default=None)
    duration_minutes_from: Optional[int] = Field(alias="durationMinutesFrom", default=None)
    duration_minutes_to: Optional[int] = Field(alias="durationMinutesTo", default=None)
    commentary: Optional[list[Commentary]] = None
    country: Optional[str] = None

    @field_validator("locale")
    @classmethod
    def _validate_locale(cls, v: str) -> str:
        """Validate locale is a valid BCP 47 language tag."""
        if not langcodes.tag_is_valid(v):
            raise ValueError(f"Invalid BCP 47 language tag: {v!r}")
        return v

    @field_validator("time_zone")
    @classmethod
    def _validate_time_zone(cls, v: str) -> str:
        """Validate time_zone is a valid IANA timezone."""
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Invalid IANA timezone: {v!r}") from exc
        return v

    @field_validator("reference")
    @classmethod
    def _validate_reference(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _REFERENCE_RE.match(v):
            raise ValueError(
                f"reference must be null or a short alphanumeric code (≤20 chars), got {v!r}"
            )
        return v
