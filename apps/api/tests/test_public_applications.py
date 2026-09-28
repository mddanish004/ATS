import json
from datetime import UTC, datetime

import pytest
from sqlmodel import select

from app.core.config import settings
from app.main import app
from app.models import (
    Application,
    ApplicationStage,
    Candidate,
    Job,
    JobApprovalStatus,
    JobStatus,
    Organization,
    ResumeDocument,
)
from app.services.storage_service import StorageError, get_private_storage

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def _organization(session, name: str, slug: str) -> Organization:
    organization = Organization(name=name, slug=slug)
    session.add(organization)
    session.commit()
    session.refresh(organization)
    return organization


def _job(session, organization: Organization, slug: str, status: JobStatus) -> Job:
    job = Job(
        organization_id=organization.id,
        title="Backend Engineer",
        slug=slug,
        status=status,
        approval_status=JobApprovalStatus.APPROVED,
        published_at=datetime.now(UTC) if status == JobStatus.PUBLISHED else None,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _payload(email: str = "candidate@example.com", **overrides) -> dict:
    payload = {
        "first_name": "Casey",
        "last_name": "Candidate",
        "email": email,
        "phone": "+1 555 0100",
        "location": "Berlin",
        "linkedin_url": "https://linkedin.com/in/casey",
        "github_url": "https://github.com/casey",
        "portfolio_url": "https://casey.example.com",
        "current_title": "Software Engineer",
        "current_company": "Example Co",
        "skills": ["Python", "SQL"],
        "years_of_experience": 5,
        "education": ["BSc Computer Science"],
        "certifications": ["AWS Developer"],
        "source": "careers_page",
        "consent_status": True,
        "cover_letter": "I would like to join the team.",
        "screening_answers": {"work_authorized": True},
    }
    payload.update(overrides)
    return payload


def _apply(client, organization_slug: str, job_slug: str, payload=None, file=None):
    return client.post(
        f"/api/v1/{organization_slug}/jobs/{job_slug}/apply",
        data={"payload": json.dumps(payload or _payload())},
        files={"resume": file or ("resume.pdf", PDF, "application/pdf")},
    )


def test_unauthenticated_application_creates_tenant_scoped_records(
    client, session, private_storage
):
    organization = _organization(session, "Acme", "acme")
    job = _job(session, organization, "backend-engineer", JobStatus.PUBLISHED)

    response = _apply(client, "acme", "backend-engineer")

    assert response.status_code == 201
    assert response.json() == {"message": "Application received"}
    candidate = session.exec(select(Candidate)).one()
    application = session.exec(select(Application)).one()
    resume = session.exec(select(ResumeDocument)).one()
    assert candidate.organization_id == organization.id
    assert candidate.email == "candidate@example.com"
    assert application.organization_id == organization.id
    assert application.candidate_id == candidate.id
    assert application.job_id == job.id
    assert application.stage == ApplicationStage.APPLIED
    assert resume.organization_id == organization.id
    assert resume.candidate_id == candidate.id
    assert resume.application_id == application.id
    assert resume.storage_key.startswith(
        f"org/{organization.id}/candidates/{candidate.id}/resumes/"
    )
    assert (private_storage.root / resume.storage_key).read_bytes() == PDF
    assert "storage_key" not in response.json()
    assert "url" not in response.json()


@pytest.mark.parametrize(
    "job_status",
    [
        JobStatus.DRAFT,
        JobStatus.PENDING_APPROVAL,
        JobStatus.APPROVED,
        JobStatus.ON_HOLD,
        JobStatus.CLOSED,
        JobStatus.ARCHIVED,
    ],
)
def test_non_published_jobs_reject_applications(client, session, job_status):
    organization = _organization(session, "Acme", "acme")
    _job(session, organization, "backend-engineer", job_status)

    response = _apply(client, "acme", "backend-engineer")

    assert response.status_code == 404
    assert session.exec(select(Candidate)).all() == []
    assert session.exec(select(Application)).all() == []


def test_duplicate_application_is_rejected(client, session, private_storage):
    organization = _organization(session, "Acme", "acme")
    _job(session, organization, "backend-engineer", JobStatus.PUBLISHED)

    assert _apply(client, "acme", "backend-engineer").status_code == 201
    duplicate = _apply(client, "acme", "backend-engineer")

    assert duplicate.status_code == 409
    assert len(session.exec(select(Candidate)).all()) == 1
    assert len(session.exec(select(Application)).all()) == 1
    assert len(session.exec(select(ResumeDocument)).all()) == 1
    assert len(list(private_storage.root.rglob("*.pdf"))) == 1


def test_candidate_can_apply_to_different_jobs_in_same_organization(client, session):
    organization = _organization(session, "Acme", "acme")
    _job(session, organization, "backend-engineer", JobStatus.PUBLISHED)
    _job(session, organization, "platform-engineer", JobStatus.PUBLISHED)

    assert _apply(client, "acme", "backend-engineer").status_code == 201
    assert _apply(client, "acme", "platform-engineer").status_code == 201

    assert len(session.exec(select(Candidate)).all()) == 1
    assert len(session.exec(select(Application)).all()) == 2


def test_same_email_is_isolated_between_organizations(client, session):
    acme = _organization(session, "Acme", "acme")
    globex = _organization(session, "Globex", "globex")
    _job(session, acme, "engineer", JobStatus.PUBLISHED)
    _job(session, globex, "engineer", JobStatus.PUBLISHED)

    assert _apply(client, "acme", "engineer").status_code == 201
    assert _apply(client, "globex", "engineer").status_code == 201

    candidates = session.exec(
        select(Candidate).order_by(Candidate.organization_id)
    ).all()
    assert len(candidates) == 2
    assert {candidate.organization_id for candidate in candidates} == {
        acme.id,
        globex.id,
    }


def test_payload_cannot_override_organization_or_stage(client, session):
    acme = _organization(session, "Acme", "acme")
    globex = _organization(session, "Globex", "globex")
    _job(session, acme, "engineer", JobStatus.PUBLISHED)
    payload = _payload(
        organization_id=str(globex.id),
        stage="hired",
        candidate_id="00000000-0000-0000-0000-000000000000",
    )

    response = _apply(client, "acme", "engineer", payload=payload)

    assert response.status_code == 422
    assert session.exec(select(Candidate)).all() == []
    assert session.exec(select(Application)).all() == []


@pytest.mark.parametrize(
    "file",
    [
        ("resume.txt", PDF, "application/pdf"),
        ("resume.pdf", PDF, "text/plain"),
        ("resume.pdf", b"not a pdf", "application/pdf"),
    ],
)
def test_invalid_resume_is_rejected(client, session, file):
    organization = _organization(session, "Acme", "acme")
    _job(session, organization, "engineer", JobStatus.PUBLISHED)

    response = _apply(client, "acme", "engineer", file=file)

    assert response.status_code == 422
    assert session.exec(select(Application)).all() == []


def test_oversized_resume_is_rejected(client, session, monkeypatch):
    organization = _organization(session, "Acme", "acme")
    _job(session, organization, "engineer", JobStatus.PUBLISHED)
    monkeypatch.setattr(settings, "max_resume_size_bytes", 10)

    response = _apply(client, "acme", "engineer")

    assert response.status_code == 413
    assert session.exec(select(Candidate)).all() == []
    assert session.exec(select(Application)).all() == []


def test_wrong_organization_slug_cannot_submit_to_job(client, session):
    acme = _organization(session, "Acme", "acme")
    _organization(session, "Globex", "globex")
    _job(session, acme, "engineer", JobStatus.PUBLISHED)

    response = _apply(client, "globex", "engineer")

    assert response.status_code == 404
    assert session.exec(select(Application)).all() == []


def test_storage_failure_rolls_back_candidate_and_application(client, session):
    class FailingStorage:
        def put(self, key: str, data: bytes) -> None:
            raise StorageError("unavailable")

        def delete(self, key: str) -> None:
            pass

    organization = _organization(session, "Acme", "acme")
    _job(session, organization, "engineer", JobStatus.PUBLISHED)
    app.dependency_overrides[get_private_storage] = lambda: FailingStorage()

    response = _apply(client, "acme", "engineer")

    assert response.status_code == 503
    assert session.exec(select(Candidate)).all() == []
    assert session.exec(select(Application)).all() == []
    assert session.exec(select(ResumeDocument)).all() == []
