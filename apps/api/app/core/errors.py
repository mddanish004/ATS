"""Consistent API error envelope, request IDs, and global exception handling.

Every API failure returns the PRD error envelope::

    {"error": {"code": "...", "message": "...", "request_id": "..."}}

Design notes:

- Intentional ``HTTPException`` responses (401/403/404/409/422/503 raised
  throughout the Day 1-6 routes) keep their status codes and messages;
  this module only wraps them in the envelope and attaches the request ID.
- Request validation failures become 422 ``VALIDATION_ERROR`` envelopes.
  The raw ``input``/``ctx`` payloads from Pydantic are deliberately
  dropped: ``input`` can echo secrets such as passwords.
- Database failures are split deliberately, not blindly mapped: transient
  infrastructure failures (connection loss, timeouts) become 503
  ``SERVICE_UNAVAILABLE``; constraint races become 409 ``CONFLICT``;
  anything else from SQLAlchemy is treated as a programmer error and
  becomes a controlled 500.
- Unexpected exceptions become a controlled 500 ``INTERNAL_ERROR`` with a
  generic message. Only the exception class name, method, path, and
  request ID are logged -- never ``str(exc)``, which may carry SQL text,
  credentials, tokens, or other sensitive details.
"""

import logging
import re
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    SQLAlchemyError,
    TimeoutError,
)
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
MAX_REQUEST_ID_LENGTH = 128
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")

_STATUS_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}

# Transient infrastructure failures: the database is unreachable, not wrong.
_TRANSIENT_DB_ERRORS: tuple[type[SQLAlchemyError], ...] = (
    OperationalError,
    DisconnectionError,
    InterfaceError,
    TimeoutError,
    DBAPIError,
)


def _is_valid_request_id(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= MAX_REQUEST_ID_LENGTH
        and bool(_REQUEST_ID_PATTERN.fullmatch(value))
    )


def request_id_from_request(request: Request) -> str:
    """Return the request ID bound by middleware, generating a fallback."""
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str) and request_id:
        return request_id
    return uuid.uuid4().hex


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Bind and echo a request ID for every HTTP request.

    An incoming ``X-Request-ID`` is honored when it looks like an opaque
    token; otherwise a random one is generated. The ID is available to
    handlers via ``request.state.request_id`` and is always echoed back
    in the response header so callers can correlate failures with logs.

    The middleware also converts any otherwise-unhandled exception into
    the controlled 500 envelope. This runs here -- rather than only in
    ``add_exception_handler(Exception, ...)`` -- so the response keeps
    the request-ID header. Anything handled downstream (HTTP, validation,
    and database errors) never reaches this ``except`` block.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()
        request.state.request_id = (
            incoming if _is_valid_request_id(incoming) else uuid.uuid4().hex
        )
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 - catch-all is the point: map to 500
            response = _unexpected_error_response(request, exc)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response


def error_response(
    request: Request, *, status_code: int, code: str, message: str
) -> JSONResponse:
    """Build a PRD error envelope for the given request."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id_from_request(request),
            }
        },
    )


def _envelope(
    request: Request, *, status_code: int, code: str, message: str
) -> JSONResponse:
    return error_response(request, status_code=status_code, code=code, message=message)


def _code_for_status(status_code: int) -> str:
    return _STATUS_CODES.get(status_code, "ERROR")


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    response = _envelope(
        request,
        status_code=exc.status_code,
        code=_code_for_status(exc.status_code),
        message=exc.detail if isinstance(exc.detail, str) else "Request failed",
    )
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def request_validation_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Drop ``input``/``ctx``: they echo the submitted payload, which may
    # contain passwords or other sensitive values. Only location, rule,
    # and message are safe to return.
    failures = [
        {
            "loc": list(error.get("loc", ())),
            "msg": error.get("msg", ""),
            "type": error.get("type", ""),
        }
        for error in exc.errors()
    ]
    count = len(failures)
    return _envelope(
        request,
        status_code=422,
        code="VALIDATION_ERROR",
        message=f"Request validation failed ({count} error{'s' if count != 1 else ''})",
    )


async def sqlalchemy_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    assert isinstance(exc, SQLAlchemyError)
    error_name = type(exc).__name__
    if isinstance(exc, IntegrityError):
        logger.warning(
            "database constraint conflict request_id=%s method=%s path=%s error=%s",
            request_id_from_request(request),
            request.method,
            request.url.path,
            error_name,
        )
        return _envelope(
            request,
            status_code=409,
            code="CONFLICT",
            message="Resource conflict",
        )
    if isinstance(exc, _TRANSIENT_DB_ERRORS):
        logger.warning(
            "database unavailable request_id=%s method=%s path=%s error=%s",
            request_id_from_request(request),
            request.method,
            request.url.path,
            error_name,
        )
        return _envelope(
            request,
            status_code=503,
            code="SERVICE_UNAVAILABLE",
            message="Service temporarily unavailable",
        )
    logger.exception(
        "database error request_id=%s method=%s path=%s error=%s",
        request_id_from_request(request),
        request.method,
        request.url.path,
        error_name,
    )
    return _envelope(
        request,
        status_code=500,
        code="INTERNAL_ERROR",
        message="An unexpected error occurred",
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Last-resort path (reached via ServerErrorMiddleware for anything that
    # escapes the middleware above). Shares the same builder so the
    # response contract is identical with or without the request-ID header.
    return _unexpected_error_response(request, exc)


def _unexpected_error_response(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unhandled error request_id=%s method=%s path=%s error=%s",
        request_id_from_request(request),
        request.method,
        request.url.path,
        type(exc).__name__,
    )
    return _envelope(
        request,
        status_code=500,
        code="INTERNAL_ERROR",
        message="An unexpected error occurred",
    )


def register_error_handlers(app: FastAPI) -> None:
    """Attach request-ID propagation and global error handlers to the app."""
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(
        RequestValidationError, request_validation_exception_handler
    )
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
