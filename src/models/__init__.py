"""OCTO data models — re-exports all public models and enums."""

from models.product import (
    AvailabilityType,
    CancellationCutoffUnit,
    ContactField,
    DeliveryFormat,
    DeliveryMethod,
    Option,
    OptionRestrictions,
    Product,
    RedemptionMethod,
    Unit,
    UnitRestrictions,
    UnitType,
)

from models.availability import (
    AvailabilityCalendarRequest,
    AvailabilityRequest,
    AvailabilitySlot,
    AvailabilityStatus,
    CalendarEntry,
    OpeningHours,
)

from models.errors import ErrorResponse

__all__ = [
    # Product enums
    "AvailabilityType",
    "CancellationCutoffUnit",
    "ContactField",
    "DeliveryFormat",
    "DeliveryMethod",
    "RedemptionMethod",
    "UnitType",
    # Product models
    "UnitRestrictions",
    "Unit",
    "OptionRestrictions",
    "Option",
    "Product",
    # Availability enums
    "AvailabilityStatus",
    # Availability models
    "OpeningHours",
    "CalendarEntry",
    "AvailabilitySlot",
    # Request models
    "AvailabilityCalendarRequest",
    "AvailabilityRequest",
    # Error models
    "ErrorResponse",
]
