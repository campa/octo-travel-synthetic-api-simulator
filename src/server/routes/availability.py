"""Availability endpoints: POST /availability/calendar, POST /availability."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from models.availability import AvailabilityCalendarRequest, AvailabilityRequest
from server.error_handler import _get_request_body_summary, create_error_response
from state.manager import StateManager

if TYPE_CHECKING:
    from telemetry.setup import TelemetryInstruments


def _parse_calendar_request(body: dict) -> AvailabilityCalendarRequest | None:
    """Parse and validate calendar request body, returning None on failure."""
    try:
        return AvailabilityCalendarRequest(**body)
    except Exception:
        return None


def _parse_availability_request(body: dict) -> AvailabilityRequest | None:
    """Parse and validate availability request body, returning None on failure."""
    try:
        return AvailabilityRequest(**body)
    except Exception:
        return None


def _validate_product_option(state: StateManager, product_id: str, option_id: str, request, body_summary: str):
    """Validate productId and optionId. Returns (product, error_response)."""
    product = state.get_product(product_id)
    if product is None:
        return None, create_error_response(
            status_code=400,
            error_code="INVALID_PRODUCT_ID",
            message="The productId was missing or invalid",
            request=request,
            body_summary=body_summary,
        )
    if not any(o.id == option_id for o in product.options):
        return None, create_error_response(
            status_code=400,
            error_code="INVALID_OPTION_ID",
            message="The optionId was missing or invalid",
            request=request,
            body_summary=body_summary,
        )
    return product, None


def create_availability_router(
    state: StateManager,
    telemetry: "TelemetryInstruments | None" = None,
) -> APIRouter:
    router = APIRouter()

    @router.post("/availability/calendar")
    async def check_calendar(request: Request) -> JSONResponse:
        body_summary = await _get_request_body_summary(request)

        try:
            body = await request.json()
        except Exception:
            body = {}

        req = _parse_calendar_request(body)
        if req is None:
            return create_error_response(
                status_code=400,
                error_code="BAD_REQUEST",
                message="Missing or invalid required fields: productId, optionId, localDateStart, localDateEnd",
                request=request,
                body_summary=body_summary,
            )

        _, err = _validate_product_option(state, req.product_id, req.option_id, request, body_summary)
        if err is not None:
            return err

        # Record availability query metric
        if telemetry:
            telemetry.product_availability_queries_total.add(
                1,
                {
                    "product_id": req.product_id,
                    "option_id": req.option_id,
                    "endpoint": "POST /availability/calendar",
                },
            )

        start = date.fromisoformat(req.local_date_start)
        end = date.fromisoformat(req.local_date_end)
        entries = state.get_calendar(req.product_id, req.option_id, start, end)

        return JSONResponse(
            content=[e.model_dump(by_alias=True) for e in (entries or [])],
        )

    @router.post("/availability")
    async def check_availability(request: Request) -> JSONResponse:
        body_summary = await _get_request_body_summary(request)

        try:
            body = await request.json()
        except Exception:
            body = {}

        req = _parse_availability_request(body)
        if req is None:
            return create_error_response(
                status_code=400,
                error_code="BAD_REQUEST",
                message="Missing or invalid required fields: productId, optionId",
                request=request,
                body_summary=body_summary,
            )

        _, err = _validate_product_option(state, req.product_id, req.option_id, request, body_summary)
        if err is not None:
            return err

        # Determine query mode
        has_dates = req.local_date_start is not None and req.local_date_end is not None
        has_ids = req.availability_ids is not None and len(req.availability_ids) > 0

        if not has_dates and not has_ids:
            return create_error_response(
                status_code=400,
                error_code="BAD_REQUEST",
                message="either localDate, localDateStart/localDateEnd or availabilityIds is required",
                request=request,
                body_summary=body_summary,
            )

        # Record availability query metric
        if telemetry:
            telemetry.product_availability_queries_total.add(
                1,
                {
                    "product_id": req.product_id,
                    "option_id": req.option_id,
                    "endpoint": "POST /availability",
                },
            )

        if has_ids:
            slots = state.get_slots_by_ids(req.product_id, req.option_id, req.availability_ids)
        else:
            start = date.fromisoformat(req.local_date_start)
            end = date.fromisoformat(req.local_date_end)
            slots = state.get_slots(req.product_id, req.option_id, start, end)

        return JSONResponse(
            content=[s.model_dump(by_alias=True) for s in (slots or [])],
        )

    return router
