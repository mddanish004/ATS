"""Week 1 vertical slice: register -> org -> job -> publish -> apply -> queue.

Exercises the actual public HTTP contracts end to end using the existing
TestClient fixtures (SQLite, local storage, in-memory queue). No live
Redis, R2, or Docker required.
"""

import json
from uuid import UUID

from app.core.security import (
    get_email_verification_expires_at,
    hash_email_verification_token,
)
from app.models import (
    Application,
    Candidate,
    EmailVerificationToken,
    ResumeDocument,
    ResumeProcessingStatus,
    User,
)
from app.services.resume_processing import process_resume_document
from sqlmodel import select

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"

EMAIL = "founder@example.com"
PASSWORD = "CorrectHorseBatteryStaple9"


def _application_payload() -> dict:
    return {
        "first_name": "Casey",
        "last_name": "Candidate",
        "email": "candidate@example.com",
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


def test_week1_vertical_slice(
    client, session, private_storage, resume_processing_queue
):
    # 1. Register a user.
    register = client.post(
        "/api/v1/auth/register",
        json={
            "email": EMAIL,
            "password": PASSWORD,
            "first_name": "Ada",
            "last_name": "Admin",
        },
    )
    assert register.status_code == 201

    # 2. Verify email using the existing hash-only token contract.
    user = session.exec(select(User).where(User.email == EMAIL)).one()
    raw_token = "week1-slice-verification-token"
    session.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=hash_email_verification_token(raw_token),
            expires_at=get_email_verification_expires_at(),
        )
    )
    session.commit()
    verify = client.post("/api/v1/auth/verify-email", json={"token": raw_token})
    assert verify.status_code == 200

    login = client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # 3-4. Create an organization (caller becomes admin member).
    org_response = client.post(
        "/api/v1/organizations", json={"name": "Acme Inc"}, headers=headers
    )
    assert org_response.status_code == 201
    organization_id = UUID(org_response.json()["id"])
    organization_slug = org_response.json()["slug"]

    # 5. Create a job.
    job_response = client.post(
        "/api/v1/jobs",
        params={"organization_id": str(organization_id)},
        headers=headers,
        json={"title": "Backend Engineer", "slug": "backend-engineer"},
    )
    assert job_response.status_code == 201
    job_id = job_response.json()["id"]
    job_slug = job_response.json()["slug"]

    def _transition(action: str) -> dict:
        response = client.post(
            f"/api/v1/jobs/{job_id}/{action}",
            params={"organization_id": str(organization_id)},
            headers=headers,
        )
        assert response.status_code == 200
        return response.json()

    # 6-8. Submit for approval, approve, publish.
    assert _transition("submit-for-approval")["status"] == "pending_approval"
    assert _transition("approve")["status"] == "approved"
    assert _transition("publish")["status"] == "published"

    # 9. Public careers listing shows the published job.
    listing = client.get(f"/api/v1/{organization_slug}/jobs")
    assert listing.status_code == 200
    assert job_slug in [job["slug"] for job in listing.json()]

    # 10. Public job detail is reachable without authentication.
    detail = client.get(f"/api/v1/{organization_slug}/jobs/{job_slug}")
    assert detail.status_code == 200
    assert detail.json()["slug"] == job_slug

    # 11-12. Submit a public application with a minimal valid PDF.
    apply = client.post(
        f"/api/v1/{organization_slug}/jobs/{job_slug}/apply",
        data={"payload": json.dumps(_application_payload())},
        files={"resume": ("resume.pdf", PDF, "application/pdf")},
    )
    assert apply.status_code == 201
    assert apply.json() == {"message": "Application received"}

    # 13-14. Application + ResumeDocument created, resume QUEUED.
    candidate = session.exec(select(Candidate)).one()
    application = session.exec(select(Application)).one()
    resume = session.exec(select(ResumeDocument)).one()
    assert application.candidate_id == candidate.id
    assert resume.candidate_id == candidate.id
    assert resume.application_id == application.id
    assert resume.organization_id == organization_id
    assert resume.status == ResumeProcessingStatus.QUEUED

    # 15. Tenant-scoped storage key and stored bytes.
    assert resume.storage_key == (
        f"org/{organization_id}/candidates/{candidate.id}/resumes/{resume.id}/original.pdf"
    )
    assert (private_storage.root / resume.storage_key).read_bytes() == PDF

    # 16. Fake queue received the deterministic task id.
    expected_task_id = f"resume-processing:{organization_id}:{resume.id}"
    assert resume_processing_queue.jobs == [
        (organization_id, resume.id, expected_task_id)
    ]

    # 17-18. Drive the existing processing path to the placeholder outcome.
    outcome = process_resume_document(
        session,
        task_id=expected_task_id,
        organization_id=organization_id,
        resume_document_id=resume.id,
    )
    assert outcome == ResumeProcessingStatus.NEEDS_REVIEW
    session.refresh(resume)
    assert resume.status == ResumeProcessingStatus.NEEDS_REVIEW
    assert resume.error_message == "Resume parsing is not implemented yet"
