"""Tests for StateManager — product storage and retrieval."""

from models.product import (
    AvailabilityType,
    Option,
    Product,
    Unit,
    UnitType,
)
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
        sm = StateManager()
        products = [_make_product("p1"), _make_product("p2")]
        sm.load_products(products)
        assert len(sm.get_all_products()) == 2

    def test_get_product_by_id(self):
        sm = StateManager()
        sm.load_products([_make_product("p1")])
        assert sm.get_product("p1") is not None
        assert sm.get_product("p1").id == "p1"

    def test_get_product_missing(self):
        sm = StateManager()
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
        sm = StateManager()
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
        sm = StateManager()
        try:
            sm.load_products([product])
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Duplicate Unit ID" in str(e)
