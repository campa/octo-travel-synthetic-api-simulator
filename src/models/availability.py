"""OCTO Availability models and request models."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AvailabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    FREESALE = "FREESALE"
    SOLD_OUT = "SOLD_OUT"
    LIMITED = "LIMITED"
    CLOSED = "CLOSED"


class OpeningHours(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_time: str = Field(alias="from")
    to_time: str = Field(alias="to")


class CalendarEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    local_date: str = Field(alias="localDate")
    available: bool
    status: AvailabilityStatus
    vacancies: Optional[int] = None
    capacity: Optional[int] = None
    opening_hours: list[OpeningHours] = Field(alias="openingHours", default_factory=list)


class AvailabilitySlot(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    local_date_time_start: str = Field(alias="localDateTimeStart")
    local_date_time_end: str = Field(alias="localDateTimeEnd")
    all_day: bool = Field(alias="allDay")
    available: bool
    status: AvailabilityStatus
    vacancies: Optional[int] = None
    capacity: Optional[int] = None
    max_units: Optional[int] = Field(alias="maxUnits", default=None)
    utc_cutoff_at: str = Field(alias="utcCutoffAt")
    opening_hours: list[OpeningHours] = Field(alias="openingHours", default_factory=list)


# Request models

class AvailabilityCalendarRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    product_id: str = Field(alias="productId")
    option_id: str = Field(alias="optionId")
    local_date_start: str = Field(alias="localDateStart")
    local_date_end: str = Field(alias="localDateEnd")


class AvailabilityRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    product_id: str = Field(alias="productId")
    option_id: str = Field(alias="optionId")
    local_date_start: Optional[str] = Field(alias="localDateStart", default=None)
    local_date_end: Optional[str] = Field(alias="localDateEnd", default=None)
    availability_ids: Optional[list[str]] = Field(alias="availabilityIds", default=None)
