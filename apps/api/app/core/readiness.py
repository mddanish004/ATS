"""Liveness vs readiness probes for the deployment slice.

- ``/health`` (in ``app.main``) means the process is alive; it checks
  nothing else.
- ``/ready`` means the required infrastructure is reachable: PostgreSQL
  (``SELECT 1`` over the existing engine) and Redis (TCP connect to the
  configured host/port).

Both checks are cheap and validate reachability only: no authentication
against Redis, no expensive queries, and no connection strings, SQL text,
or credentials in responses or logs -- only up/down booleans.
"""

import logging
import socket
from urllib.parse import urlparse

from app.core.config import settings
from app.db.database import engine
from sqlalchemy import text

logger = logging.getLogger(__name__)


def check_database() -> bool:
    """Return True when PostgreSQL answers a trivial query."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - any DB failure means not ready
        return False


def check_redis(timeout_seconds: float = 2.0) -> bool:
    """Return True when the configured Redis host accepts a TCP connection."""
    try:
        parsed = urlparse(settings.redis_url)
        if parsed.scheme not in {"redis", "rediss"}:
            return False
        host = parsed.hostname or "localhost"
        port = parsed.port or 6379
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except Exception:  # noqa: BLE001 - any Redis failure means not ready
        return False
