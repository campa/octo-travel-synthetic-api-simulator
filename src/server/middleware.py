"""Request metrics middleware for tracking per-endpoint and per-product counters."""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

if TYPE_CHECKING:
    from telemetry.setup import TelemetryInstruments

logger = logging.getLogger("otas.middleware")

# Pattern to extract product_id from URL path
_PRODUCT_ID_PATTERN = re.compile(r"/products/([^/]+)")


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    """Track request counts and durations via OTel instruments."""

    def __init__(self, app, telemetry: TelemetryInstruments | None = None) -> None:
        super().__init__(app)
        self._tel = telemetry

    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start_time

        endpoint = f"{request.method} {request.url.path}"
        status_code = response.status_code

        if self._tel:
            self._tel.server_requests_total.add(
                1, {"endpoint": endpoint, "status_code": str(status_code)}
            )
            self._tel.server_request_duration_seconds.record(
                duration, {"endpoint": endpoint}
            )

            if status_code >= 400:
                error_type = response.headers.get("X-Error-Code", "UNKNOWN")
                self._tel.server_errors_total.add(
                    1,
                    {
                        "endpoint": endpoint,
                        "status_code": str(status_code),
                        "error_type": error_type,
                    },
                )
            if status_code >= 500:
                self._tel.server_5xx_errors_total.add(
                    1, {"endpoint": endpoint}
                )

        # Extract product_id from path if present
        match = _PRODUCT_ID_PATTERN.search(request.url.path)
        if match and self._tel:
            product_id = match.group(1)
            self._tel.product_requests_total.add(
                1, {"product_id": product_id, "endpoint": endpoint}
            )

        logger.debug(
            "request endpoint=%s status=%d duration=%.4fs",
            endpoint,
            status_code,
            duration,
        )

        return response
