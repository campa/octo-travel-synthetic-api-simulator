"""In-memory state store with availability generation."""

from __future__ import annotations

import logging
import random
import uuid
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from telemetry.setup import TelemetryInstruments

from models.availability import (
    AvailabilitySlot,
    AvailabilityStatus,
    CalendarEntry,
    OpeningHours,
)
from models.product import AvailabilityType, Product

logger = logging.getLogger(__name__)


class StateManager:
    """Single source of truth for all OCTO entities and availability data."""

    def __init__(
        self,
        availability_days: int = 90,
        telemetry: "TelemetryInstruments | None" = None,
    ) -> None:
        self._availability_days = availability_days
        self._products: dict[str, Product] = {}
        self._calendar: dict[tuple[str, str, str], CalendarEntry] = {}
        self._slots: dict[tuple[str, str, str], list[AvailabilitySlot]] = {}
        self._slots_by_id: dict[str, AvailabilitySlot] = {}
        self._tel = telemetry

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_products(self, products: list[Product]) -> None:
        """Store products (with uniqueness checks) and generate availability."""
        for product in products:
            self._validate_id_uniqueness(product)
            self._products[product.id] = product

        self._generate_availability()

        # Update telemetry gauges
        if self._tel:
            self._tel.server_products_count.add(len(self._products))
            self._tel.server_availability_slots_count.add(len(self._slots_by_id))

    def get_all_products(self) -> list[Product]:
        return list(self._products.values())

    def get_product(self, product_id: str) -> Optional[Product]:
        return self._products.get(product_id)

    def get_calendar(
        self,
        product_id: str,
        option_id: str,
        start: date,
        end: date,
    ) -> Optional[list[CalendarEntry]]:
        """Return calendar entries for a product-option pair within [start, end]."""
        product = self._products.get(product_id)
        if product is None:
            return None
        if not any(o.id == option_id for o in product.options):
            return None

        entries: list[CalendarEntry] = []
        current = start
        while current <= end:
            key = (product_id, option_id, current.isoformat())
            entry = self._calendar.get(key)
            if entry is not None:
                entries.append(entry)
            current += timedelta(days=1)
        return entries

    def get_slots(
        self,
        product_id: str,
        option_id: str,
        start: date,
        end: date,
    ) -> Optional[list[AvailabilitySlot]]:
        """Return availability slots for a product-option pair within [start, end]."""
        product = self._products.get(product_id)
        if product is None:
            return None
        if not any(o.id == option_id for o in product.options):
            return None

        result: list[AvailabilitySlot] = []
        current = start
        while current <= end:
            key = (product_id, option_id, current.isoformat())
            result.extend(self._slots.get(key, []))
            current += timedelta(days=1)
        return result

    def get_slots_by_ids(
        self,
        product_id: str,
        option_id: str,
        ids: list[str],
    ) -> Optional[list[AvailabilitySlot]]:
        """Return slots matching the given IDs for a product-option pair."""
        product = self._products.get(product_id)
        if product is None:
            return None
        if not any(o.id == option_id for o in product.options):
            return None

        return [self._slots_by_id[sid] for sid in ids if sid in self._slots_by_id]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_id_uniqueness(product: Product) -> None:
        """Verify Option IDs unique within Product, Unit IDs unique within Option."""
        option_ids: set[str] = set()
        for option in product.options:
            if option.id in option_ids:
                raise ValueError(
                    f"Duplicate Option ID '{option.id}' in Product '{product.id}'"
                )
            option_ids.add(option.id)

            unit_ids: set[str] = set()
            for unit in option.units:
                if unit.id in unit_ids:
                    raise ValueError(
                        f"Duplicate Unit ID '{unit.id}' in Option '{option.id}'"
                    )
                unit_ids.add(unit.id)

    # ------------------------------------------------------------------
    # Availability generation
    # ------------------------------------------------------------------

    # Weighted status distribution
    _STATUS_WEIGHTS: list[tuple[AvailabilityStatus, float]] = [
        (AvailabilityStatus.AVAILABLE, 0.60),
        (AvailabilityStatus.LIMITED, 0.15),
        (AvailabilityStatus.SOLD_OUT, 0.10),
        (AvailabilityStatus.CLOSED, 0.10),
        (AvailabilityStatus.FREESALE, 0.05),
    ]

    def _generate_availability(self) -> None:
        """Generate calendar + slot data for every product-option pair."""
        statuses = [s for s, _ in self._STATUS_WEIGHTS]
        weights = [w for _, w in self._STATUS_WEIGHTS]
        today = date.today()

        for product in self._products.values():
            for option in product.options:
                slot_count = 0
                available_days = 0
                for day_offset in range(self._availability_days):
                    current_date = today + timedelta(days=day_offset)
                    date_str = current_date.isoformat()
                    status = random.choices(statuses, weights=weights, k=1)[0]

                    self._generate_day(
                        product, option, current_date, date_str, status
                    )

                    key = (product.id, option.id, date_str)
                    day_slots = self._slots.get(key, [])
                    slot_count += len(day_slots)
                    if day_slots:
                        available_days += 1

                # Emit per-product-option gauges
                if self._tel:
                    labels = {"product_id": product.id, "option_id": option.id}
                    self._tel.product_availability_slots_count.add(
                        slot_count, labels
                    )
                    self._tel.product_availability_days_count.add(
                        available_days, labels
                    )

    def _generate_day(
        self,
        product: Product,
        option,
        current_date: date,
        date_str: str,
        status: AvailabilityStatus,
    ) -> None:
        """Generate calendar entry + slots for a single day."""
        key = (product.id, option.id, date_str)

        # --- CLOSED: no slots, zero vacancies ---
        if status == AvailabilityStatus.CLOSED:
            self._calendar[key] = CalendarEntry(
                local_date=date_str,
                available=False,
                status=status,
                vacancies=0,
                capacity=0,
            )
            self._slots[key] = []
            return

        # --- FREESALE: null vacancies/capacity ---
        if status == AvailabilityStatus.FREESALE:
            slots = self._make_slots(
                product, option, current_date, date_str, status,
                vacancies=None, capacity=None,
            )
            self._calendar[key] = CalendarEntry(
                local_date=date_str,
                available=True,
                status=status,
                vacancies=None,
                capacity=None,
            )
            self._slots[key] = slots
            for s in slots:
                self._slots_by_id[s.id] = s
            return

        # --- AVAILABLE / LIMITED / SOLD_OUT ---
        capacity = random.randint(20, 200)

        if status == AvailabilityStatus.AVAILABLE:
            vacancies = random.randint(max(1, capacity // 2), capacity)
        elif status == AvailabilityStatus.LIMITED:
            vacancies = random.randint(1, max(1, capacity // 2 - 1))
        else:  # SOLD_OUT
            vacancies = 0

        slots = self._make_slots(
            product, option, current_date, date_str, status,
            vacancies=vacancies, capacity=capacity,
        )

        # For START_TIME, calendar vacancies = sum of slot vacancies
        if product.availability_type == AvailabilityType.START_TIME and slots:
            cal_vacancies = sum(s.vacancies for s in slots if s.vacancies is not None)
        else:
            cal_vacancies = vacancies

        available = status not in (
            AvailabilityStatus.SOLD_OUT,
            AvailabilityStatus.CLOSED,
        )

        self._calendar[key] = CalendarEntry(
            local_date=date_str,
            available=available,
            status=status,
            vacancies=cal_vacancies,
            capacity=capacity,
        )
        self._slots[key] = slots
        for s in slots:
            self._slots_by_id[s.id] = s

    def _make_slots(
        self,
        product: Product,
        option,
        current_date: date,
        date_str: str,
        status: AvailabilityStatus,
        vacancies: Optional[int],
        capacity: Optional[int],
    ) -> list[AvailabilitySlot]:
        """Build slot list for a single day based on product availability type."""
        is_sold_out = status == AvailabilityStatus.SOLD_OUT

        if product.availability_type == AvailabilityType.OPENING_HOURS:
            return self._make_opening_hours_slot(
                option, current_date, date_str, status,
                vacancies, capacity, is_sold_out,
            )

        # START_TIME
        return self._make_start_time_slots(
            option, current_date, date_str, status,
            vacancies, capacity, is_sold_out,
        )

    def _make_opening_hours_slot(
        self,
        option,
        current_date: date,
        date_str: str,
        status: AvailabilityStatus,
        vacancies: Optional[int],
        capacity: Optional[int],
        is_sold_out: bool,
    ) -> list[AvailabilitySlot]:
        """One all-day slot with openingHours populated."""
        slot_id = str(uuid.uuid4())
        start_dt = datetime.combine(current_date, datetime.min.time())
        end_dt = datetime.combine(current_date, datetime.max.time().replace(microsecond=0))

        slot = AvailabilitySlot(
            id=slot_id,
            local_date_time_start=start_dt.isoformat(),
            local_date_time_end=end_dt.isoformat(),
            all_day=True,
            available=not is_sold_out,
            status=status,
            vacancies=0 if is_sold_out else vacancies,
            capacity=capacity,
            max_units=capacity,
            utc_cutoff_at=start_dt.isoformat() + "Z",
            opening_hours=[
                OpeningHours(from_time="09:00", to_time="17:00"),
            ],
        )
        return [slot]

    def _make_start_time_slots(
        self,
        option,
        current_date: date,
        date_str: str,
        status: AvailabilityStatus,
        vacancies: Optional[int],
        capacity: Optional[int],
        is_sold_out: bool,
    ) -> list[AvailabilitySlot]:
        """One slot per availabilityLocalStartTimes entry."""
        start_times = option.availability_local_start_times
        if not start_times:
            start_times = ["09:00"]

        num_slots = len(start_times)

        # Distribute vacancies across slots
        slot_vacancies = self._distribute_vacancies(vacancies, num_slots, is_sold_out)
        # Distribute capacity across slots
        slot_capacities = self._distribute_vacancies(capacity, num_slots, False)

        slots: list[AvailabilitySlot] = []
        for i, time_str in enumerate(start_times):
            slot_id = str(uuid.uuid4())
            hour, minute = (int(x) for x in time_str.split(":"))
            start_dt = datetime.combine(
                current_date, datetime.min.time().replace(hour=hour, minute=minute)
            )
            end_dt = start_dt + timedelta(hours=1)

            sv = slot_vacancies[i]
            sc = slot_capacities[i]

            slot = AvailabilitySlot(
                id=slot_id,
                local_date_time_start=start_dt.isoformat(),
                local_date_time_end=end_dt.isoformat(),
                all_day=False,
                available=not is_sold_out and (sv is None or sv > 0),
                status=status,
                vacancies=sv,
                capacity=sc,
                max_units=sc,
                utc_cutoff_at=start_dt.isoformat() + "Z",
            )
            slots.append(slot)
        return slots

    @staticmethod
    def _distribute_vacancies(
        total: Optional[int],
        num_slots: int,
        force_zero: bool,
    ) -> list[Optional[int]]:
        """Distribute a total across num_slots, preserving the sum invariant."""
        if total is None:
            return [None] * num_slots
        if force_zero or total == 0:
            return [0] * num_slots
        if num_slots == 1:
            return [total]

        # Give each slot at least 0, distribute remainder randomly
        parts = [0] * num_slots
        remaining = total
        for i in range(num_slots - 1):
            parts[i] = random.randint(0, remaining)
            remaining -= parts[i]
        parts[-1] = remaining
        return parts
