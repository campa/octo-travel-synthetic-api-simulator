"""OCTO Product, Option, Unit Pydantic models and related enums."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AvailabilityType(str, Enum):
    START_TIME = "START_TIME"
    OPENING_HOURS = "OPENING_HOURS"


class DeliveryFormat(str, Enum):
    PDF_URL = "PDF_URL"
    QRCODE = "QRCODE"


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


class UnitRestrictions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    min_age: int = Field(alias="minAge", default=0)
    max_age: int = Field(alias="maxAge", default=100)
    id_required: bool = Field(alias="idRequired", default=False)
    min_quantity: Optional[int] = Field(alias="minQuantity", default=None)
    max_quantity: Optional[int] = Field(alias="maxQuantity", default=None)
    pax_count: int = Field(alias="paxCount", default=1)
    accompanied_by: list[str] = Field(alias="accompaniedBy", default_factory=list)


class Unit(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    internal_name: str = Field(alias="internalName")
    reference: Optional[str] = None
    type: UnitType
    required_contact_fields: list[ContactField] = Field(
        alias="requiredContactFields", default_factory=list
    )
    restrictions: UnitRestrictions = Field(default_factory=UnitRestrictions)


class OptionRestrictions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    min_units: Optional[int] = Field(alias="minUnits", default=None)
    max_units: Optional[int] = Field(alias="maxUnits", default=None)


class Option(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    default: bool = False
    internal_name: str = Field(alias="internalName")
    reference: Optional[str] = None
    availability_local_start_times: list[str] = Field(
        alias="availabilityLocalStartTimes", default_factory=list
    )
    cancellation_cutoff: str = Field(alias="cancellationCutoff", default="0 hours")
    cancellation_cutoff_amount: int = Field(alias="cancellationCutoffAmount", default=0)
    cancellation_cutoff_unit: CancellationCutoffUnit = Field(
        alias="cancellationCutoffUnit", default=CancellationCutoffUnit.HOUR
    )
    required_contact_fields: list[ContactField] = Field(
        alias="requiredContactFields", default_factory=list
    )
    restrictions: OptionRestrictions = Field(default_factory=OptionRestrictions)
    units: list[Unit] = Field(default_factory=list)


class Product(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    internal_name: str = Field(alias="internalName")
    reference: Optional[str] = None
    locale: str = "en"
    time_zone: str = Field(alias="timeZone", default="Europe/London")
    allow_freesale: bool = Field(alias="allowFreesale", default=False)
    instant_confirmation: bool = Field(alias="instantConfirmation", default=True)
    instant_delivery: bool = Field(alias="instantDelivery", default=True)
    availability_required: bool = Field(alias="availabilityRequired", default=True)
    availability_type: AvailabilityType = Field(
        alias="availabilityType", default=AvailabilityType.START_TIME
    )
    delivery_formats: list[DeliveryFormat] = Field(
        alias="deliveryFormats", default_factory=list
    )
    delivery_methods: list[DeliveryMethod] = Field(
        alias="deliveryMethods", default_factory=list
    )
    redemption_method: RedemptionMethod = Field(
        alias="redemptionMethod", default=RedemptionMethod.DIGITAL
    )
    options: list[Option] = Field(default_factory=list)
