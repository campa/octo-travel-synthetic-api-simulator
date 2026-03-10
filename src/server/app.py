"""FastAPI application factory."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from common.config import Settings
from server.error_handler import create_error_response
from server.routes.products import create_products_router
from server.routes.availability import create_availability_router
from server.middleware import RequestMetricsMiddleware
from state.manager import StateManager
from telemetry.setup import TelemetryInstruments

logger = logging.getLogger("otas.app")


def create_app(
    state: StateManager,
    settings: Settings,
    telemetry: TelemetryInstruments | None = None,
) -> FastAPI:
    """Factory that wires routes, middleware, and error handlers."""
    app = FastAPI(title="OCTO Travel Mock API", version="0.1.0")

    # Store state on app for route access
    app.state.manager = state
    app.state.settings = settings
    app.state.telemetry = telemetry

    # Register routes
    app.include_router(create_products_router(state, telemetry))
    app.include_router(create_availability_router(state, telemetry))

    # Request metrics middleware
    app.add_middleware(RequestMetricsMiddleware, telemetry=telemetry)

    # Unhandled exception handler → 500 INTERNAL_ERROR
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        body_summary = ""
        try:
            body = await request.body()
            body_summary = body.decode("utf-8", errors="replace")[:1024]
        except Exception:
            pass
        return create_error_response(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message=str(exc),
            request=request,
            body_summary=body_summary,
        )

    # Ensure Content-Type: application/json on all responses
    @app.middleware("http")
    async def set_json_content_type(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Type"] = "application/json"
        return response

    return app
