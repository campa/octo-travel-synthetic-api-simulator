"""Tests for FastAPI server: app factory and product routes."""

import uuid

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
    sm = StateManager()
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
