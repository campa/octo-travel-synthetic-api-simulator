"""Error handling with Error_Instance_ID correlation and structured logging."""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import JSONResponse

from models.errors import ErrorResponse

logger = logging.getLogger("otas.error_handler")


async def _get_request_body_summary(request: Request, max_length: int = 1024) -> str:
    """Read and truncate the request body for logging."""
    try:
        body = await request.body()
        text = body.decode("utf-8", errors="replace")
        if len(text) > max_length:
            return text[:max_length] + "..."
        return text
    except Exception:
        return "<unavailable>"


def create_error_response(
    status_code: int,
    error_code: str,
    message: str,
    request: Request,
    body_summary: str = "",
) -> JSONResponse:
    """Build a JSON error response with Error_Instance_ID correlation.

    Generates a UUID v4 error ID, sets X-Error-Id and X-Error-Code headers,
    writes a structured grep-friendly log entry, and returns a JSONResponse.
    """
    error_id = str(uuid.uuid4())
    endpoint = f"{request.method} {request.url.path}"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # Structured, grep-friendly log line
    logger.error(
        "%s | ERROR | error_id=%s | error_code=%s | status=%d | endpoint=%s | body=%s | msg=%s",
        timestamp,
        error_id,
        error_code,
        status_code,
        endpoint,
        body_summary,
        message,
    )

    error_response = ErrorResponse(
        error=error_code,
        error_message=message,
        error_id=error_id,
    )

    return JSONResponse(
        status_code=status_code,
        content=error_response.model_dump(by_alias=True),
        headers={
            "X-Error-Id": error_id,
            "X-Error-Code": error_code,
        },
    )
