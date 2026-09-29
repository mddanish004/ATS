import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_router
from app.core import readiness
from app.core.config import settings
from app.core.errors import (
    error_response,
    register_error_handlers,
    request_id_from_request,
)

logger = logging.getLogger(__name__)


def health_check() -> dict[str, str]:
    """Liveness: the process is alive. Checks no dependencies."""
    return {"status": "ok"}


def readiness_check(request: Request) -> dict[str, object] | JSONResponse:
    """Readiness: PostgreSQL and Redis are reachable.

    Returns 200 when both are up, otherwise a 503 error envelope. Only
    up/down booleans leave this process: failed checks are logged
    server-side with the request ID and without connection details.
    """
    database_ok = readiness.check_database()
    redis_ok = readiness.check_redis()
    if database_ok and redis_ok:
        return {"status": "ready", "checks": {"database": "up", "redis": "up"}}
    logger.warning(
        "readiness failed database_up=%s redis_up=%s request_id=%s",
        database_ok,
        redis_ok,
        request_id_from_request(request),
    )
    return error_response(
        request,
        status_code=503,
        code="SERVICE_UNAVAILABLE",
        message="Service not ready",
    )


def create_app() -> FastAPI:
    """Build the FastAPI application (also used by deployment tests)."""
    application = FastAPI(title=settings.app_name)
    if settings.cors_allowed_origins:
        # CORS is opt-in: with no configured origins the API is
        # same-origin only. Added before the error middleware so
        # failures also carry CORS headers.
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=settings.cors_allow_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    register_error_handlers(application)
    application.include_router(api_router, prefix="/api/v1")
    application.get("/health")(health_check)
    # response_model=None: the 200 shape and the 503 envelope share one route.
    application.get("/ready", response_model=None)(readiness_check)
    return application


app = create_app()
