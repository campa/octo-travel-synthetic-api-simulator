"""Tests for FastAPI server: app factory, product routes, availability routes, middleware."""

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from common.config import Settings
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
from server.app import create_app
from state.manager import StateManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_product(
    availability_type: AvailabilityType = AvailabilityType.START_TIME,
    start_times: list[str] | None = None,
) -> Product:
    """Build a minimal valid Product for testing."""
    if start_times is None:
        start_times = ["09:00", "14:00"]
    return Product(
        id=str(uuid.uuid4()),
        internal_name="Test Tour",
        locale="en",
        time_zone="Europe/London",
        availability_type=availability_type,
        delivery_formats=[DeliveryFormat.QRCODE],
        delivery_methods=[DeliveryMethod.TICKET],
        redemption_method=RedemptionMethod.DIGITAL,
        options=[
            Option(
                id=str(uuid.uuid4()),
                default=True,
                internal_name="Standard",
                availability_local_start_times=start_times,
                units=[
                    Unit(id=str(uuid.uuid4()), internal_name="Adult", type=UnitType.ADULT),
                ],
            )
        ],
    )


@pytest.fixture
def state_with_products() -> StateManager:
    sm = StateManager(availability_days=5)
    sm.load_products([_make_product(), _make_product(AvailabilityType.OPENING_HOURS, ["09:00"])])
    return sm


@pytest.fixture
def client(state_with_products: StateManager) -> TestClient:
    app = create_app(state_with_products, Settings())
    return TestClient(app)


@pytest.fixture
def products(state_with_products: StateManager) -> list[Product]:
    return state_with_products.get_all_products()


# ---------------------------------------------------------------------------
# App factory tests
# ---------------------------------------------------------------------------

class TestAppFactory:
    def test_create_app_returns_fastapi(self, state_with_products):
        app = create_app(state_with_products, Settings())
        assert app is not None
        assert app.title == "OCTO Travel Mock API"

    def test_json_content_type_on_all_responses(self, client):
        resp = client.get("/products")
        assert resp.headers["content-type"] == "application/json"

    def test_unhandled_exception_returns_500(self, state_with_products):
        app = create_app(state_with_products, Settings())

        @app.get("/boom")
        async def boom():
            raise RuntimeError("kaboom")

        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.get("/boom")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"] == "INTERNAL_ERROR"
        assert "errorId" in body
        assert "X-Error-Id" in resp.headers
        assert resp.headers["X-Error-Code"] == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# GET /products
# ---------------------------------------------------------------------------

class TestListProducts:
    def test_returns_200_with_array(self, client, products):
        resp = client.get("/products")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == len(products)

    def test_products_have_camel_case_keys(self, client):
        resp = client.get("/products")
        product = resp.json()[0]
        assert "internalName" in product
        assert "availabilityType" in product
        assert "deliveryFormats" in product
        assert "options" in product

    def test_products_include_nested_options_and_units(self, client):
        resp = client.get("/products")
        product = resp.json()[0]
        assert len(product["options"]) >= 1
        option = product["options"][0]
        assert "internalName" in option
        assert len(option["units"]) >= 1
        unit = option["units"][0]
        assert "internalName" in unit
        assert "type" in unit


# ---------------------------------------------------------------------------
# GET /products/{product_id}
# ---------------------------------------------------------------------------

class TestGetProduct:
    def test_valid_id_returns_200(self, client, products):
        pid = products[0].id
        resp = client.get(f"/products/{pid}")
        assert resp.status_code == 200
        assert resp.json()["id"] == pid

    def test_invalid_id_returns_404(self, client):
        resp = client.get(f"/products/{uuid.uuid4()}")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"] == "INVALID_PRODUCT_ID"
        assert "errorId" in body
        assert "X-Error-Id" in resp.headers

    def test_single_product_matches_list(self, client, products):
        """Cross-endpoint consistency: list element == single fetch."""
        list_resp = client.get("/products")
        for item in list_resp.json():
            single_resp = client.get(f"/products/{item['id']}")
            assert single_resp.json() == item


# ---------------------------------------------------------------------------
# POST /availability/calendar
# ---------------------------------------------------------------------------

class TestAvailabilityCalendar:
    def test_valid_request_returns_200(self, client, products):
        p = products[0]
        o = p.options[0]
        today = date.today().isoformat()
        end = (date.today() + timedelta(days=2)).isoformat()
        resp = client.post("/availability/calendar", json={
            "productId": p.id,
            "optionId": o.id,
            "localDateStart": today,
            "localDateEnd": end,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) <= 3  # up to 3 days

    def test_calendar_entries_have_required_fields(self, client, products):
        p = products[0]
        o = p.options[0]
        today = date.today().isoformat()
        resp = client.post("/availability/calendar", json={
            "productId": p.id,
            "optionId": o.id,
            "localDateStart": today,
            "localDateEnd": today,
        })
        assert resp.status_code == 200
        for entry in resp.json():
            assert "localDate" in entry
            assert "available" in entry
            assert "status" in entry

    def test_invalid_product_id_returns_400(self, client, products):
        o = products[0].options[0]
        resp = client.post("/availability/calendar", json={
            "productId": str(uuid.uuid4()),
            "optionId": o.id,
            "localDateStart": "2026-01-01",
            "localDateEnd": "2026-01-02",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PRODUCT_ID"

    def test_invalid_option_id_returns_400(self, client, products):
        p = products[0]
        resp = client.post("/availability/calendar", json={
            "productId": p.id,
            "optionId": str(uuid.uuid4()),
            "localDateStart": "2026-01-01",
            "localDateEnd": "2026-01-02",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_OPTION_ID"

    def test_missing_fields_returns_400(self, client):
        resp = client.post("/availability/calendar", json={"productId": "x"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "BAD_REQUEST"


# ---------------------------------------------------------------------------
# POST /availability
# ---------------------------------------------------------------------------

class TestAvailability:
    def test_date_range_query_returns_200(self, client, products):
        p = products[0]
        o = p.options[0]
        today = date.today().isoformat()
        end = (date.today() + timedelta(days=1)).isoformat()
        resp = client.post("/availability", json={
            "productId": p.id,
            "optionId": o.id,
            "localDateStart": today,
            "localDateEnd": end,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_slots_have_required_fields(self, client, products):
        p = products[0]
        o = p.options[0]
        today = date.today().isoformat()
        resp = client.post("/availability", json={
            "productId": p.id,
            "optionId": o.id,
            "localDateStart": today,
            "localDateEnd": today,
        })
        for slot in resp.json():
            assert "id" in slot
            assert "localDateTimeStart" in slot
            assert "localDateTimeEnd" in slot
            assert "allDay" in slot
            assert "status" in slot

    def test_availability_ids_query(self, client, products):
        p = products[0]
        o = p.options[0]
        today = date.today().isoformat()
        # First get some slot IDs
        slots_resp = client.post("/availability", json={
            "productId": p.id,
            "optionId": o.id,
            "localDateStart": today,
            "localDateEnd": today,
        })
        slots = slots_resp.json()
        if slots:
            target_ids = [slots[0]["id"]]
            resp = client.post("/availability", json={
                "productId": p.id,
                "optionId": o.id,
                "availabilityIds": target_ids,
            })
            assert resp.status_code == 200
            assert len(resp.json()) <= len(target_ids)

    def test_invalid_product_id_returns_400(self, client, products):
        o = products[0].options[0]
        resp = client.post("/availability", json={
            "productId": str(uuid.uuid4()),
            "optionId": o.id,
            "localDateStart": "2026-01-01",
            "localDateEnd": "2026-01-02",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PRODUCT_ID"

    def test_invalid_option_id_returns_400(self, client, products):
        p = products[0]
        resp = client.post("/availability", json={
            "productId": p.id,
            "optionId": str(uuid.uuid4()),
            "localDateStart": "2026-01-01",
            "localDateEnd": "2026-01-02",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_OPTION_ID"

    def test_missing_date_range_and_ids_returns_400(self, client, products):
        p = products[0]
        o = p.options[0]
        resp = client.post("/availability", json={
            "productId": p.id,
            "optionId": o.id,
        })
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"] == "BAD_REQUEST"
        assert "availabilityIds" in body["errorMessage"]

    def test_missing_body_fields_returns_400(self, client):
        resp = client.post("/availability", json={})
        assert resp.status_code == 400
        assert resp.json()["error"] == "BAD_REQUEST"


# ---------------------------------------------------------------------------
# Error correlation
# ---------------------------------------------------------------------------

class TestErrorCorrelation:
    def test_error_responses_have_correlation_headers(self, client):
        resp = client.get(f"/products/{uuid.uuid4()}")
        assert resp.status_code == 404
        body = resp.json()
        assert body["errorId"] == resp.headers["X-Error-Id"]
        assert body["error"] == resp.headers["X-Error-Code"]

    def test_error_ids_are_unique(self, client):
        ids = set()
        for _ in range(5):
            resp = client.get(f"/products/{uuid.uuid4()}")
            ids.add(resp.json()["errorId"])
        assert len(ids) == 5
