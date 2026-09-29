"""Tests for the Day 7 Task 4 deployment slice.

Covers /health, /ready (healthy/unhealthy, without live infrastructure),
CORS behavior, and production environment validation. No Docker, Redis,
or Neon required: infrastructure checks use dependency mocking plus
real code paths against unroutable local endpoints.
"""

import pytest
from app.core import readiness
from app.core.config import Settings, settings
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine

REQUEST_ID_HEADER = "X-Request-ID"

_PROD_DB = "postgresql://deploy:secret@ep-neon-12345.us-east-2.aws.neon.tech/hireflow"
_PROD_REDIS = "redis://managed-redis.example.com:6379/0"
_LONG_SECRET = "deployment-test-secret-at-least-32-chars"


def _settings(**overrides):
    params = {
        "database_url": _PROD_DB,
        "jwt_secret_key": _LONG_SECRET,
        "redis_url": _PROD_REDIS,
    }
    params.update(overrides)
    return Settings(**params)


def test_health_reports_liveness(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers.get(REQUEST_ID_HEADER)


def test_ready_reports_healthy_dependencies(client, monkeypatch):
    monkeypatch.setattr(readiness, "check_database", lambda: True)
    monkeypatch.setattr(readiness, "check_redis", lambda: True)

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "up", "redis": "up"},
    }
    assert response.headers.get(REQUEST_ID_HEADER)


def test_ready_is_503_when_database_is_down(client, monkeypatch):
    monkeypatch.setattr(readiness, "check_database", lambda: False)
    monkeypatch.setattr(readiness, "check_redis", lambda: True)

    response = client.get("/ready")

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "SERVICE_UNAVAILABLE"
    assert error["message"] == "Service not ready"
    assert error["request_id"] == response.headers.get(REQUEST_ID_HEADER)


def test_ready_is_503_when_redis_is_down(client, monkeypatch):
    monkeypatch.setattr(readiness, "check_database", lambda: True)
    monkeypatch.setattr(readiness, "check_redis", lambda: False)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


def test_ready_never_leaks_connection_details(client, monkeypatch):
    monkeypatch.setattr(readiness, "check_database", lambda: False)
    monkeypatch.setattr(readiness, "check_redis", lambda: False)

    response = client.get("/ready")

    assert response.status_code == 503
    for secret in ("password", "passwd", "neon", "5432", "6379", "Traceback"):
        assert secret not in response.text


def test_database_check_fails_closed_without_live_postgres(monkeypatch):
    monkeypatch.setattr(
        readiness,
        "engine",
        create_engine("sqlite:////nonexistent-dir-hireflow-xyz/db.sqlite"),
    )

    assert readiness.check_database() is False


def test_redis_check_fails_closed_on_closed_port(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:1/0")

    assert readiness.check_redis(timeout_seconds=0.2) is False


def test_redis_check_rejects_non_redis_schemes(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "postgres://localhost:5432/db")

    assert readiness.check_redis() is False


def _local_client(monkeypatch, origins):
    monkeypatch.setattr(settings, "cors_allowed_origins", origins)
    return TestClient(create_app())


def test_cors_allows_configured_origin(client, monkeypatch):
    local_client = _local_client(monkeypatch, ["https://app.example.com"])

    response = local_client.get(
        "/health", headers={"Origin": "https://app.example.com"}
    )

    assert response.status_code == 200
    assert (
        response.headers.get("access-control-allow-origin") == "https://app.example.com"
    )


def test_cors_preflight_succeeds_for_configured_origin(monkeypatch):
    local_client = _local_client(monkeypatch, ["https://app.example.com"])

    response = local_client.options(
        "/health",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert (
        response.headers.get("access-control-allow-origin") == "https://app.example.com"
    )


def test_cors_blocks_unlisted_origin(monkeypatch):
    local_client = _local_client(monkeypatch, ["https://app.example.com"])

    response = local_client.get(
        "/health", headers={"Origin": "https://evil.example.com"}
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_cors_disabled_by_default(client):
    response = client.get("/health", headers={"Origin": "https://app.example.com"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_production_rejects_wildcard_cors():
    with pytest.raises(ValidationError):
        _settings(environment="production", cors_allowed_origins=["*"])


def test_production_rejects_short_jwt_secret():
    with pytest.raises(ValidationError):
        _settings(environment="production", jwt_secret_key="too-short")


def test_production_rejects_non_postgres_database():
    with pytest.raises(ValidationError):
        _settings(environment="production", database_url="sqlite:///local.db")


def test_production_rejects_localhost_redis():
    with pytest.raises(ValidationError):
        _settings(environment="production", redis_url="redis://localhost:6379/0")


def test_valid_production_settings_are_accepted():
    resolved = _settings(environment="production")

    assert resolved.environment == "production"
    assert resolved.cors_allowed_origins == []


def test_development_allows_wildcard_cors():
    resolved = _settings(environment="development", cors_allowed_origins=["*"])

    assert resolved.cors_allowed_origins == ["*"]


def test_app_env_alias_selects_environment(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("APP_ENV", "production")

    resolved = _settings()

    assert resolved.environment == "production"


def test_cors_origins_parse_from_comma_separated_env(monkeypatch):
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "https://app.example.com, https://admin.example.com",
    )

    resolved = _settings(environment="development")

    assert resolved.cors_allowed_origins == [
        "https://app.example.com",
        "https://admin.example.com",
    ]
