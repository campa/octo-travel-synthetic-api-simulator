"""Product endpoints: GET /products, GET /products/{product_id}."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from models.errors import ErrorResponse
from models.product import Product
from server.error_handler import create_error_response
from state.manager import StateManager

if TYPE_CHECKING:
    from telemetry.setup import TelemetryInstruments


def create_products_router(
    state: StateManager,
    telemetry: "TelemetryInstruments | None" = None,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/products",
        response_model=list[Product],
        summary="List Products",
        description="Returns all available products.",
    )
    async def list_products() -> JSONResponse:
        products = state.get_all_products()
        return JSONResponse(
            content=[p.model_dump(by_alias=True) for p in products],
        )

    @router.get(
        "/products/{id}",
        response_model=Product,
        responses={404: {"model": ErrorResponse, "description": "Product not found"}},
        summary="Get Product",
        description="Returns a single product by its ID.",
    )
    async def get_product(id: str, request: Request) -> JSONResponse:
        product = state.get_product(id)
        if product is None:
            return create_error_response(
                status_code=404,
                error_code="INVALID_PRODUCT_ID",
                message="The productId was missing or invalid",
                request=request,
                body_summary="",
            )
        return JSONResponse(content=product.model_dump(by_alias=True))

    return router
