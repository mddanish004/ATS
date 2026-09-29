"""Focused tests for the Day 7 Task 2 error-handling hardening.

Covers the PRD error envelope, request-ID generation/propagation, global
exception handling without sensitive leakage, and preservation of the
established 401/403/404/409/422/503 semantics.
"""

import json
from datetime import UTC, datetime
from uuid import UUID

from app.db.database import get_session
from app.main import app
from app.models import (
    Job,
    JobApprovalStatus,
    JobStatus,
    Organization,
)
from app.services.resume_queue import ResumeQueueError, get_resume_processing_queue
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import select

REQUEST_ID_HEADER = "X-Request-ID"
EMAIL = "envelope@example.com"
PASSWORD = "CorrectHorseBatteryStaple9"
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def _error(response):
    assert response.headers.get(REQUEST_ID_HEADER)
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert body["error"]["request_id"] == response.headers.get(REQUEST_ID_HEADER)
    return body["error"]


def _register(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": EMAIL,
            "password": PASSWORD,
            "first_name": "Ada",
            "last_name": "Lovelace",
        },
    )
    assert response.status_code == 201
    return response


def test_success_responses_carry_generated_request_id(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers.get(REQUEST_ID_HEADER)


def test_error_envelope_shape_and_code(client, auth_headers, missing_organization_id):
    response = client.get(
        f"/api/v1/organizations/{missing_organization_id}", headers=auth_headers
    )

    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "NOT_FOUND"
    assert error["message"] == "Organization not found"


def test_incoming_request_id_is_propagated(
    client, auth_headers, missing_organization_id
):
    response = client.get(
        f"/api/v1/organizations/{missing_organization_id}",
        headers={**auth_headers, REQUEST_ID_HEADER: "caller-req-1"},
    )

    assert response.status_code == 404
    error = _error(response)
    assert error["request_id"] == "caller-req-1"


def test_invalid_request_id_is_replaced(client, auth_headers, missing_organization_id):
    response = client.get(
        f"/api/v1/organizations/{missing_organization_id}",
        headers={**auth_headers, REQUEST_ID_HEADER: "not a valid id!!!"},
    )

    assert response.status_code == 404
    error = _error(response)
    assert error["request_id"] != "not a valid id!!!"
    assert error["request_id"]


def test_validation_errors_use_envelope_without_echoing_secrets(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "not-an-email",
            "password": "short",
            "first_name": "Ada",
            "last_name": "Lovelace",
        },
    )

    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "VALIDATION_ERROR"
    assert error["message"]
    # The submitted password must not be echoed back in any form.
    assert "short" not in response.text
    assert '"input"' not in response.text


def test_unexpected_errors_become_controlled_500_without_leakage(client):
    def _boom():
        raise RuntimeError("FATAL postgres://admin:s3cret@db.internal:5432/ats")

    app.dependency_overrides[get_session] = _boom

    response = client.post("/api/v1/organizations", json={"name": "Acme"})

    assert response.status_code == 500
    error = _error(response)
    assert error["code"] == "INTERNAL_ERROR"
    assert error["message"] == "An unexpected error occurred"
    assert "s3cret" not in response.text
    assert "db.internal" not in response.text
    assert "Traceback" not in response.text


def test_operational_db_errors_become_503_without_leakage(client):
    def _unavailable():
        raise OperationalError(
            "SELECT 1",
            {},
            Exception("could not connect to db.internal password=s3cret"),
        )

    app.dependency_overrides[get_session] = _unavailable

    response = client.post("/api/v1/organizations", json={"name": "Acme"})

    assert response.status_code == 503
    error = _error(response)
    assert error["code"] == "SERVICE_UNAVAILABLE"
    assert error["message"] == "Service temporarily unavailable"
    assert "s3cret" not in response.text
    assert "db.internal" not in response.text


def test_integrity_errors_become_409_without_leakage(client):
    def _conflict():
        raise IntegrityError(
            "INSERT INTO organization ...",
            {},
            Exception('duplicate key value violates "organization_slug_key"'),
        )

    app.dependency_overrides[get_session] = _conflict

    response = client.post("/api/v1/organizations", json={"name": "Acme"})

    assert response.status_code == 409
    error = _error(response)
    assert error["code"] == "CONFLICT"
    assert "duplicate key" not in response.text


def test_authentication_failure_semantics_preserved(client):
    _register(client)
    response = client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": "WrongPassword99"}
    )

    assert response.status_code == 401
    error = _error(response)
    assert error["code"] == "UNAUTHORIZED"
    assert error["message"] == "Invalid email or password"


def test_unverified_forbidden_semantics_preserved(client):
    _register(client)
    access_token = client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    ).json()["access_token"]

    response = client.post(
        "/api/v1/organizations",
        json={"name": "Acme"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 403
    error = _error(response)
    assert error["code"] == "FORBIDDEN"
    assert error["message"] == "Email verification required"


def test_duplicate_registration_conflict_preserved(client):
    assert _register(client).status_code == 201
    duplicate = client.post(
        "/api/v1/auth/register",
        json={
            "email": EMAIL,
            "password": PASSWORD,
            "first_name": "Ada",
            "last_name": "Lovelace",
        },
    )

    assert duplicate.status_code == 409
    error = _error(duplicate)
    assert error["code"] == "CONFLICT"
    assert error["message"] == "Email already registered"


def test_tenant_hiding_preserved(client, session, user, auth_headers):
    other = Organization(name="Hidden Tenant", slug="hidden-tenant")
    session.add(other)
    session.commit()
    session.refresh(other)

    unknown_id = UUID("00000000-0000-0000-0000-000000000000")
    unknown = client.get(f"/api/v1/organizations/{unknown_id}", headers=auth_headers)
    foreign = client.get(f"/api/v1/organizations/{other.id}", headers=auth_headers)

    assert unknown.status_code == foreign.status_code == 404
    unknown_error = _error(unknown)
    foreign_error = _error(foreign)
    assert (
        unknown_error["message"] == foreign_error["message"] == "Organization not found"
    )
    assert unknown_error["code"] == foreign_error["code"] == "NOT_FOUND"
    assert "Hidden Tenant" not in foreign.text


def test_queue_failure_maps_to_503_envelope(client, session):
    organization = Organization(name="Acme", slug="acme")
    session.add(organization)
    session.commit()
    session.refresh(organization)
    session.add(
        Job(
            organization_id=organization.id,
            title="Backend Engineer",
            slug="backend-engineer",
            status=JobStatus.PUBLISHED,
            approval_status=JobApprovalStatus.APPROVED,
            published_at=datetime.now(UTC),
        )
    )
    session.commit()

    class FailingQueue:
        async def enqueue_resume_processing(
            self, *, organization_id, resume_document_id
        ) -> str:
            raise ResumeQueueError("redis unavailable")

    app.dependency_overrides[get_resume_processing_queue] = lambda: FailingQueue()

    response = client.post(
        "/api/v1/acme/jobs/backend-engineer/apply",
        data={
            "payload": json.dumps(
                {
                    "first_name": "Casey",
                    "last_name": "Candidate",
                    "email": "candidate@example.com",
                    "consent_status": True,
                }
            )
        },
        files={"resume": ("resume.pdf", PDF, "application/pdf")},
    )

    assert response.status_code == 503
    error = _error(response)
    assert error["code"] == "SERVICE_UNAVAILABLE"
    assert len(session.exec(select(Organization)).all()) == 1
