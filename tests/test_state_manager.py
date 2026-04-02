"""Tests for StateManager — product storage and retrieval."""

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
        reference=None,
        locale="en",
        time_zone="Europe/London",
        allow_freesale=False,
        instant_confirmation=True,
        instant_delivery=True,
        availability_required=True,
        availability_type=availability_type,
        delivery_formats=["QRCODE"],
        delivery_methods=["TICKET"],
        redemption_method="DIGITAL",
        options=[
            Option(
                id="opt-1",
                default=True,
                internal_name="Default Option",
                reference=None,
                availability_local_start_times=start_times,
                cancellation_cutoff="0 hours",
                cancellation_cutoff_amount=0,
                cancellation_cutoff_unit="hour",
                required_contact_fields=[],
                restrictions={"minUnits": 0, "maxUnits": None},
                units=[Unit(
                    id="unit-1",
                    internal_name="Adult",
                    type=UnitType.ADULT,
                    required_contact_fields=[],
                    restrictions={
                        "minAge": 18, "maxAge": 64, "idRequired": False,
                        "minQuantity": 1, "maxQuantity": None,
                        "paxCount": 1, "accompaniedBy": [],
                    },
                )],
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
            reference=None,
            locale="en",
            time_zone="Europe/London",
            allow_freesale=False,
            instant_confirmation=True,
            instant_delivery=True,
            availability_required=True,
            availability_type="START_TIME",
            delivery_formats=["QRCODE"],
            delivery_methods=["TICKET"],
            redemption_method="DIGITAL",
            options=[
                Option(
                    id="dup", default=True, internal_name="A", reference=None,
                    availability_local_start_times=["09:00"],
                    cancellation_cutoff="0 hours", cancellation_cutoff_amount=0,
                    cancellation_cutoff_unit="hour", required_contact_fields=[],
                    restrictions={"minUnits": 0, "maxUnits": None}, units=[],
                ),
                Option(
                    id="dup", default=False, internal_name="B", reference=None,
                    availability_local_start_times=["09:00"],
                    cancellation_cutoff="0 hours", cancellation_cutoff_amount=0,
                    cancellation_cutoff_unit="hour", required_contact_fields=[],
                    restrictions={"minUnits": 0, "maxUnits": None}, units=[],
                ),
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
            reference=None,
            locale="en",
            time_zone="Europe/London",
            allow_freesale=False,
            instant_confirmation=True,
            instant_delivery=True,
            availability_required=True,
            availability_type="START_TIME",
            delivery_formats=["QRCODE"],
            delivery_methods=["TICKET"],
            redemption_method="DIGITAL",
            options=[
                Option(
                    id="opt-1", default=True, internal_name="A", reference=None,
                    availability_local_start_times=["09:00"],
                    cancellation_cutoff="0 hours", cancellation_cutoff_amount=0,
                    cancellation_cutoff_unit="hour", required_contact_fields=[],
                    restrictions={"minUnits": 0, "maxUnits": None},
                    units=[
                        Unit(
                            id="dup", internal_name="U1", type=UnitType.ADULT,
                            required_contact_fields=[],
                            restrictions={"minAge": 18, "maxAge": 64, "idRequired": False,
                                "minQuantity": 1, "maxQuantity": None, "paxCount": 1, "accompaniedBy": []},
                        ),
                        Unit(
                            id="dup", internal_name="U2", type=UnitType.CHILD,
                            required_contact_fields=[],
                            restrictions={"minAge": 3, "maxAge": 12, "idRequired": False,
                                "minQuantity": 1, "maxQuantity": None, "paxCount": 1, "accompaniedBy": []},
                        ),
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
