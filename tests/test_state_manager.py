"""Smoke tests for StateManager — storage, retrieval, and availability generation."""

from datetime import date, timedelta

from models.product import (
    AvailabilityType,
    Option,
    Product,
    Unit,
    UnitType,
)
from models.availability import AvailabilityStatus
from state.manager import StateManager


def _make_product(
    product_id: str = "prod-1",
    availability_type: AvailabilityType = AvailabilityType.START_TIME,
    start_times: list[str] | None = None,
) -> Product:
    if start_times is None:
        start_times = ["09:00", "14:00"]
    return Product(
        id=product_id,
        internal_name="Test Product",
        availability_type=availability_type,
        options=[
            Option(
                id="opt-1",
                internal_name="Default Option",
                availability_local_start_times=start_times,
                units=[Unit(id="unit-1", internal_name="Adult", type=UnitType.ADULT)],
            )
        ],
    )


class TestStateManagerStorage:
    def test_load_and_retrieve_all(self):
        sm = StateManager(availability_days=5)
        products = [_make_product("p1"), _make_product("p2")]
        sm.load_products(products)
        assert len(sm.get_all_products()) == 2

    def test_get_product_by_id(self):
        sm = StateManager(availability_days=5)
        sm.load_products([_make_product("p1")])
        assert sm.get_product("p1") is not None
        assert sm.get_product("p1").id == "p1"

    def test_get_product_missing(self):
        sm = StateManager(availability_days=5)
        sm.load_products([_make_product("p1")])
        assert sm.get_product("nonexistent") is None

    def test_duplicate_option_id_raises(self):
        product = Product(
            id="p1",
            internal_name="Bad Product",
            options=[
                Option(id="dup", internal_name="A", units=[]),
                Option(id="dup", internal_name="B", units=[]),
            ],
        )
        sm = StateManager(availability_days=1)
        try:
            sm.load_products([product])
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Duplicate Option ID" in str(e)

    def test_duplicate_unit_id_raises(self):
        product = Product(
            id="p1",
            internal_name="Bad Product",
            options=[
                Option(
                    id="opt-1",
                    internal_name="A",
                    units=[
                        Unit(id="dup", internal_name="U1", type=UnitType.ADULT),
                        Unit(id="dup", internal_name="U2", type=UnitType.CHILD),
                    ],
                )
            ],
        )
        sm = StateManager(availability_days=1)
        try:
            sm.load_products([product])
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Duplicate Unit ID" in str(e)


class TestAvailabilityGeneration:
    def test_calendar_entries_generated(self):
        sm = StateManager(availability_days=10)
        sm.load_products([_make_product()])
        today = date.today()
        end = today + timedelta(days=9)
        entries = sm.get_calendar("prod-1", "opt-1", today, end)
        assert entries is not None
        assert len(entries) == 10

    def test_calendar_returns_none_for_bad_product(self):
        sm = StateManager(availability_days=5)
        sm.load_products([_make_product()])
        today = date.today()
        assert sm.get_calendar("bad", "opt-1", today, today) is None

    def test_calendar_returns_none_for_bad_option(self):
        sm = StateManager(availability_days=5)
        sm.load_products([_make_product()])
        today = date.today()
        assert sm.get_calendar("prod-1", "bad-opt", today, today) is None

    def test_slots_generated_for_start_time(self):
        sm = StateManager(availability_days=5)
        sm.load_products([_make_product(start_times=["09:00", "14:00"])])
        today = date.today()
        end = today + timedelta(days=4)
        slots = sm.get_slots("prod-1", "opt-1", today, end)
        assert slots is not None
        assert len(slots) > 0

    def test_slots_returns_none_for_bad_product(self):
        sm = StateManager(availability_days=5)
        sm.load_products([_make_product()])
        today = date.today()
        assert sm.get_slots("bad", "opt-1", today, today) is None

    def test_slots_by_ids(self):
        sm = StateManager(availability_days=5)
        sm.load_products([_make_product()])
        today = date.today()
        end = today + timedelta(days=4)
        all_slots = sm.get_slots("prod-1", "opt-1", today, end)
        assert all_slots is not None and len(all_slots) > 0
        first_id = all_slots[0].id
        found = sm.get_slots_by_ids("prod-1", "opt-1", [first_id])
        assert found is not None
        assert len(found) == 1
        assert found[0].id == first_id

    def test_slots_by_ids_returns_none_for_bad_product(self):
        sm = StateManager(availability_days=5)
        sm.load_products([_make_product()])
        assert sm.get_slots_by_ids("bad", "opt-1", ["x"]) is None


class TestAvailabilityConsistency:
    """Verify the key consistency invariants across calendar and slots."""

    def test_closed_days_have_no_slots(self):
        sm = StateManager(availability_days=90)
        sm.load_products([_make_product()])
        today = date.today()
        for day_offset in range(90):
            d = today + timedelta(days=day_offset)
            ds = d.isoformat()
            key = ("prod-1", "opt-1", ds)
            entry = sm._calendar.get(key)
            if entry and entry.status == AvailabilityStatus.CLOSED:
                slots = sm._slots.get(key, [])
                assert len(slots) == 0, f"CLOSED day {ds} should have 0 slots"

    def test_sold_out_slots_have_zero_vacancies(self):
        sm = StateManager(availability_days=90)
        sm.load_products([_make_product()])
        today = date.today()
        for day_offset in range(90):
            d = today + timedelta(days=day_offset)
            ds = d.isoformat()
            key = ("prod-1", "opt-1", ds)
            entry = sm._calendar.get(key)
            if entry and entry.status == AvailabilityStatus.SOLD_OUT:
                for slot in sm._slots.get(key, []):
                    assert slot.vacancies == 0
                    assert slot.available is False

    def test_available_days_have_at_least_one_slot(self):
        sm = StateManager(availability_days=90)
        sm.load_products([_make_product()])
        today = date.today()
        for day_offset in range(90):
            d = today + timedelta(days=day_offset)
            ds = d.isoformat()
            key = ("prod-1", "opt-1", ds)
            entry = sm._calendar.get(key)
            if entry and entry.status in (
                AvailabilityStatus.AVAILABLE,
                AvailabilityStatus.LIMITED,
                AvailabilityStatus.FREESALE,
            ):
                slots = sm._slots.get(key, [])
                assert len(slots) >= 1, f"{entry.status} day {ds} needs ≥1 slot"

    def test_start_time_vacancy_sum_matches_calendar(self):
        sm = StateManager(availability_days=90)
        sm.load_products([_make_product()])
        today = date.today()
        for day_offset in range(90):
            d = today + timedelta(days=day_offset)
            ds = d.isoformat()
            key = ("prod-1", "opt-1", ds)
            entry = sm._calendar.get(key)
            if entry is None:
                continue
            if entry.status in (AvailabilityStatus.CLOSED, AvailabilityStatus.FREESALE):
                continue
            slots = sm._slots.get(key, [])
            slot_sum = sum(s.vacancies for s in slots if s.vacancies is not None)
            assert entry.vacancies == slot_sum, (
                f"Day {ds}: calendar vacancies={entry.vacancies} != slot sum={slot_sum}"
            )

    def test_opening_hours_single_allday_slot(self):
        sm = StateManager(availability_days=30)
        sm.load_products([
            _make_product(
                availability_type=AvailabilityType.OPENING_HOURS,
                start_times=[],
            )
        ])
        today = date.today()
        for day_offset in range(30):
            d = today + timedelta(days=day_offset)
            ds = d.isoformat()
            key = ("prod-1", "opt-1", ds)
            entry = sm._calendar.get(key)
            if entry is None or entry.status == AvailabilityStatus.CLOSED:
                continue
            slots = sm._slots.get(key, [])
            assert len(slots) == 1, f"OPENING_HOURS day {ds} should have exactly 1 slot"
            assert slots[0].all_day is True

    def test_start_time_slots_not_allday(self):
        sm = StateManager(availability_days=30)
        sm.load_products([_make_product()])
        today = date.today()
        for day_offset in range(30):
            d = today + timedelta(days=day_offset)
            ds = d.isoformat()
            key = ("prod-1", "opt-1", ds)
            slots = sm._slots.get(key, [])
            for slot in slots:
                assert slot.all_day is False
