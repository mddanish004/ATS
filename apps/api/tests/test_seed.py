"""Tests for the deterministic development seed (Day 7 Task 3)."""

import pytest
from app.core.config import settings
from app.core.security import verify_password
from app.db.seed import (
    SEED_DEV_PASSWORD,
    SEED_ORG_SLUG,
    SeedRefusedError,
    seed_development_data,
)
from app.models import (
    Application,
    ApplicationStage,
    Candidate,
    Job,
    JobStatus,
    Membership,
    MembershipRole,
    Organization,
    ResumeDocument,
    ResumeProcessingStatus,
    User,
)
from sqlmodel import select


def _counts(session):
    return {
        "organizations": len(session.exec(select(Organization)).all()),
        "users": len(session.exec(select(User)).all()),
        "memberships": len(session.exec(select(Membership)).all()),
        "jobs": len(session.exec(select(Job)).all()),
        "candidates": len(session.exec(select(Candidate)).all()),
        "applications": len(session.exec(select(Application)).all()),
        "resumes": len(session.exec(select(ResumeDocument)).all()),
    }


def test_seed_creates_expected_dataset(client, session):
    summary = seed_development_data(session)

    assert summary.created_organizations == 1
    assert summary.created_users == 4
    assert summary.created_memberships == 4
    assert summary.created_jobs == 4
    assert summary.created_candidates == 2
    assert summary.created_applications == 2
    assert summary.created_resumes == 2

    organization = session.exec(
        select(Organization).where(Organization.slug == SEED_ORG_SLUG)
    ).one()
    assert organization.name == "Acme Inc"

    roles = {
        membership.user_id: membership.role
        for membership in session.exec(select(Membership)).all()
    }
    assert len(roles) == 4
    assert set(roles.values()) == {
        MembershipRole.ADMIN,
        MembershipRole.RECRUITER,
        MembershipRole.HIRING_MANAGER,
        MembershipRole.INTERVIEWER,
    }
    for user in session.exec(select(User)).all():
        assert user.is_email_verified is True
        assert user.is_active is True
        assert user.password_hash is not None
        assert SEED_DEV_PASSWORD not in user.password_hash
        assert verify_password(SEED_DEV_PASSWORD, user.password_hash)

    jobs = {
        job.slug: job
        for job in session.exec(
            select(Job).where(Job.organization_id == organization.id)
        ).all()
    }
    assert set(jobs) == {
        "backend-engineer",
        "platform-engineer",
        "product-designer",
        "support-specialist",
    }
    assert jobs["backend-engineer"].status == JobStatus.PUBLISHED
    assert jobs["platform-engineer"].status == JobStatus.DRAFT
    assert jobs["product-designer"].status == JobStatus.PENDING_APPROVAL
    assert jobs["support-specialist"].status == JobStatus.CLOSED
    assert jobs["backend-engineer"].published_at is not None
    assert jobs["support-specialist"].closed_at is not None

    # The published job is publicly accessible without authentication.
    listing = client.get(f"/api/v1/{SEED_ORG_SLUG}/jobs")
    assert listing.status_code == 200
    assert "backend-engineer" in [job["slug"] for job in listing.json()]
    detail = client.get(f"/api/v1/{SEED_ORG_SLUG}/jobs/backend-engineer")
    assert detail.status_code == 200

    applications = session.exec(
        select(Application).where(Application.organization_id == organization.id)
    ).all()
    assert len(applications) == 2
    stages = {application.stage for application in applications}
    assert stages == {ApplicationStage.APPLIED, ApplicationStage.SCREENING}
    for application in applications:
        candidate = session.get(Candidate, application.candidate_id)
        assert candidate is not None
        assert candidate.organization_id == organization.id
        assert candidate.email == candidate.email.lower()
        job = session.get(Job, application.job_id)
        assert job is not None
        assert job.slug == "backend-engineer"

    resumes = session.exec(
        select(ResumeDocument).where(ResumeDocument.organization_id == organization.id)
    ).all()
    assert len(resumes) == 2
    for resume in resumes:
        assert resume.status == ResumeProcessingStatus.UPLOADED
        assert resume.content_type == "application/pdf"
        assert resume.storage_key == (
            f"org/{organization.id}/candidates/{resume.candidate_id}"
            f"/resumes/{resume.id}/original.pdf"
        )


def test_seed_is_idempotent(session):
    first = seed_development_data(session)
    assert first.created_jobs == 4

    before = _counts(session)
    second = seed_development_data(session)
    after = _counts(session)

    assert before == after
    assert second.created_organizations == 0
    assert second.created_users == 0
    assert second.created_memberships == 0
    assert second.created_jobs == 0
    assert second.created_candidates == 0
    assert second.created_applications == 0
    assert second.created_resumes == 0
    assert len(second.skipped) > 0


@pytest.mark.parametrize("environment", ["production", "PRODUCTION", "staging"])
def test_seed_refuses_non_development_environments(session, monkeypatch, environment):
    monkeypatch.setattr(settings, "environment", environment)

    with pytest.raises(SeedRefusedError):
        seed_development_data(session)

    assert _counts(session) == {
        "organizations": 0,
        "users": 0,
        "memberships": 0,
        "jobs": 0,
        "candidates": 0,
        "applications": 0,
        "resumes": 0,
    }


def test_seed_refuses_production_app_env(session, monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(SeedRefusedError):
        seed_development_data(session)

    assert _counts(session)["organizations"] == 0
