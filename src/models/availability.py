"""OCTO Availability Calendar Pydantic models.

Validates the POST /availability/calendar response schema with:
- Status enum and logical consistency (available ↔ status ↔ vacancies)
- Opening hours time format validation
- Extended fields (weight, pax, frequency) as optional
"""

import datetime
import re
from enum import Enum
from typing import Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class AvailabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    FREESALE = "FREESALE"
    SOLD_OUT = "SOLD_OUT"
    LIMITED = "LIMITED"
    CLOSED = "CLOSED"


_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


class OpeningHours(BaseModel):
    """A single opening-hours window for a calendar day."""

    model_config = ConfigDict(populate_by_name=True)

    from_time: str = Field(alias="from")
    to_time: str = Field(alias="to")
    frequency: Optional[str] = None
    frequency_amount: Optional[int] = Field(alias="frequencyAmount", default=None)
    frequency_unit: Optional[str] = Field(alias="frequencyUnit", default=None)

    @field_validator("from_time", "to_time")
    @classmethod
    def _validate_time_format(cls, v: str) -> str:
        if not _TIME_RE.match(v):
            raise ValueError(f"Time must be HH:MM format, got {v!r}")
        return v


class AvailabilityCalendarDay(BaseModel):
    """One day in an availability calendar response."""

    model_config = ConfigDict(populate_by_name=True)

    local_date: str = Field(alias="localDate")
    available: bool
    status: AvailabilityStatus
    status_message: Optional[str] = Field(alias="statusMessage", default=None)
    vacancies: Optional[int] = None
    capacity: Optional[int] = None
    max_units: Optional[int] = Field(alias="maxUnits", default=None)
    opening_hours: list[OpeningHours] = Field(alias="openingHours")
    utc_cutoff_at: Optional[str] = Field(alias="utcCutoffAt", default=None)
    utc_onsale_at: Optional[str] = Field(alias="utcOnsaleAt", default=None)

    # Extended fields
    availability_local_start_times: Optional[list[str]] = Field(
        alias="availabilityLocalStartTimes", default=None
    )
    available_weight: Optional[float] = Field(alias="availableWeight", default=None)
    max_weight: Optional[float] = Field(alias="maxWeight", default=None)
    weight_unit: Optional[str] = Field(alias="weightUnit", default=None)
    total_capacity: Optional[int] = Field(alias="totalCapacity", default=None)
    total_max_weight: Optional[float] = Field(alias="totalMaxWeight", default=None)
    limit_capacity: Optional[int] = Field(alias="limitCapacity", default=None)
    pax_count: Optional[int] = Field(alias="paxCount", default=None)
    pax_weight: Optional[float] = Field(alias="paxWeight", default=None)
    limit_pax_count: Optional[int] = Field(alias="limitPaxCount", default=None)
    total_pax_count: Optional[int] = Field(alias="totalPaxCount", default=None)
    total_pax_weight: Optional[float] = Field(alias="totalPaxWeight", default=None)
    no_shows: Optional[int] = Field(alias="noShows", default=None)
    total_no_shows: Optional[int] = Field(alias="totalNoShows", default=None)
    max_pax_count: Optional[int] = Field(alias="maxPaxCount", default=None)

    @field_validator("local_date")
    @classmethod
    def _validate_date_format(cls, v: str) -> str:
        try:
            datetime.date.fromisoformat(v)
        except ValueError as exc:
            raise ValueError(f"localDate must be YYYY-MM-DD, got {v!r}") from exc
        return v

    @model_validator(mode="after")
    def _check_status_consistency(self) -> "AvailabilityCalendarDay":
        """Validate logical consistency between available, status, and vacancies."""
        if self.status == AvailabilityStatus.CLOSED and self.available:
            raise ValueError("available must be false when status is CLOSED")
        if self.status == AvailabilityStatus.SOLD_OUT and self.available:
            raise ValueError("available must be false when status is SOLD_OUT")
        if self.status == AvailabilityStatus.FREESALE and self.vacancies is not None:
            raise ValueError("vacancies should be null when status is FREESALE")
        if self.status == AvailabilityStatus.LIMITED and self.available is False:
            raise ValueError("available must be true when status is LIMITED")
        if self.status == AvailabilityStatus.AVAILABLE and self.available is False:
            raise ValueError("available must be true when status is AVAILABLE")
        return self
