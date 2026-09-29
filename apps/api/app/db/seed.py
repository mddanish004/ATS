"""Deterministic development seed data for the Week 1 backend slice.

Dataset (all identifiers stable across runs):

- Organization ``acme`` ("Acme Inc")
- 4 users (admin / recruiter / hiring manager / interviewer) with a single
  documented development password (``DevPassword123``), verified emails
- 4 jobs: draft, pending-approval, published (+ publicly listed), closed
- 2 candidates + applications against the published job
- 2 ``ResumeDocument`` rows in ``UPLOADED`` status with tenant-scoped keys

What the seed deliberately does NOT do: store resume binaries, touch R2,
enqueue Redis/ARQ jobs, or run AI processing. Resume rows are database
references only.

Usage (from ``apps/api``)::

    uv run python -m app.db.seed

Safety:

- Refuses to run when the environment is production or staging.
- Idempotent: every record is looked up by its natural unique key first;
  running twice creates nothing new. All primary keys are deterministic
  UUIDv5 values derived from those same natural keys.
"""

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.core.security import hash_password
from app.db.database import engine
from app.models import (
    Application,
    ApplicationStage,
    Candidate,
    Job,
    JobApprovalStatus,
    JobStatus,
    Membership,
    MembershipRole,
    Organization,
    ResumeDocument,
    ResumeProcessingStatus,
    User,
)
from app.schemas import OrganizationCreate, UserRegister
from app.schemas.job import JobCreate
from app.services.resume_service import resume_storage_key
from pydantic import EmailStr, TypeAdapter
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

SEED_ORG_NAME = "Acme Inc"
SEED_ORG_SLUG = "acme"

# Single documented development credential. Never log this value and never
# use it outside local development.
SEED_DEV_PASSWORD = "DevPassword123"

SEED_USERS: tuple[tuple[str, str, str, MembershipRole], ...] = (
    ("admin@example.com", "Ada", "Admin", MembershipRole.ADMIN),
    ("recruiter@example.com", "Rita", "Recruiter", MembershipRole.RECRUITER),
    (
        "hiring-manager@example.com",
        "Henry",
        "Manager",
        MembershipRole.HIRING_MANAGER,
    ),
    (
        "interviewer@example.com",
        "Ivan",
        "Interviewer",
        MembershipRole.INTERVIEWER,
    ),
)

PUBLISHED_AT = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
CLOSED_AT = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

SEED_CANDIDATES: tuple[tuple[str, str, str, str, ApplicationStage], ...] = (
    (
        "casey@example.com",
        "Casey",
        "Candidate",
        "backend-engineer",
        ApplicationStage.APPLIED,
    ),
    (
        "riley@example.com",
        "Riley",
        "Applicant",
        "backend-engineer",
        ApplicationStage.SCREENING,
    ),
)

# Development reference only: no resume object is stored for seeded rows.
SEED_RESUME_SIZE_BYTES = 18_240

_SEED_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "hireflow.dev-seed.v1")
_BLOCKED_ENVIRONMENTS = frozenset({"production", "prod", "staging"})

_email_adapter = TypeAdapter(EmailStr)


class SeedRefusedError(RuntimeError):
    """Raised when seeding is attempted in a non-development environment."""


@dataclass
class SeedSummary:
    created_organizations: int = 0
    created_users: int = 0
    created_memberships: int = 0
    created_jobs: int = 0
    created_candidates: int = 0
    created_applications: int = 0
    created_resumes: int = 0
    skipped: list[str] = field(default_factory=list)


def _seed_id(*parts: str) -> UUID:
    return uuid.uuid5(_SEED_NAMESPACE, ":".join(parts))


def _environment_allows_seeding() -> bool:
    values = {
        settings.environment.strip().lower(),
        os.environ.get("APP_ENV", "").strip().lower(),
    }
    return not (values & _BLOCKED_ENVIRONMENTS)


def _require_seeding_allowed() -> None:
    if not _environment_allows_seeding():
        raise SeedRefusedError(
            "Refusing to seed: APP_ENV/environment indicates a "
            "production or staging environment"
        )


def _get_or_create_organization(session: Session, summary: SeedSummary) -> Organization:
    validated = OrganizationCreate(
        name=SEED_ORG_NAME,
        slug=SEED_ORG_SLUG,
        description="Deterministic development organization",
        timezone="UTC",
        currency="USD",
    )
    existing = session.exec(
        select(Organization).where(Organization.slug == validated.slug)
    ).first()
    if existing is not None:
        summary.skipped.append(f"organization:{existing.slug}")
        return existing
    assert validated.slug is not None  # always provided for the seed org
    organization = Organization(
        id=_seed_id("organization", validated.slug),
        name=validated.name,
        slug=validated.slug,
        description=validated.description,
        timezone=validated.timezone,
        currency=validated.currency,
    )
    session.add(organization)
    summary.created_organizations += 1
    return organization


def _get_or_create_users(
    session: Session, organization: Organization, summary: SeedSummary
) -> dict[str, User]:
    users: dict[str, User] = {}
    for email, first_name, last_name, role in SEED_USERS:
        validated = UserRegister(
            email=email,
            password=SEED_DEV_PASSWORD,
            first_name=first_name,
            last_name=last_name,
        )
        user = session.exec(
            select(User).where(User.email == str(validated.email))
        ).one_or_none()
        if user is None:
            user = User(
                id=_seed_id("user", str(validated.email)),
                email=str(validated.email),
                password_hash=hash_password(validated.password),
                first_name=validated.first_name,
                last_name=validated.last_name,
                is_email_verified=True,
                is_active=True,
            )
            session.add(user)
            summary.created_users += 1
        else:
            summary.skipped.append(f"user:{user.email}")
        users[role.value] = user

        membership = session.exec(
            select(Membership).where(
                Membership.organization_id == organization.id,
                Membership.user_id == user.id,
            )
        ).one_or_none()
        if membership is None:
            session.add(
                Membership(
                    id=_seed_id("membership", organization.slug, user.email),
                    organization_id=organization.id,
                    user_id=user.id,
                    role=role,
                )
            )
            summary.created_memberships += 1
        else:
            summary.skipped.append(f"membership:{organization.slug}:{user.email}")
    return users


def _job_payloads(recruiter_id: UUID, hiring_manager_id: UUID) -> list[dict[str, Any]]:
    return [
        {
            "title": "Backend Engineer",
            "slug": "backend-engineer",
            "department": "Engineering",
            "employment_type": "full_time",
            "location": "Remote",
            "work_mode": "remote",
            "salary_min": 100_000,
            "salary_max": 150_000,
            "currency": "USD",
            "description": "Build reliable hiring software.",
            "requirements": "Python experience.",
            "skills": ["Python", "SQL"],
            "openings": 2,
            "hiring_manager_id": hiring_manager_id,
            "recruiter_id": recruiter_id,
            "status": JobStatus.PUBLISHED,
            "approval_status": JobApprovalStatus.APPROVED,
            "published_at": PUBLISHED_AT,
        },
        {
            "title": "Platform Engineer",
            "slug": "platform-engineer",
            "department": "Engineering",
            "employment_type": "full_time",
            "location": "Remote",
            "work_mode": "remote",
            "skills": ["Python", "PostgreSQL"],
            "openings": 1,
            "status": JobStatus.DRAFT,
            "approval_status": JobApprovalStatus.NONE,
        },
        {
            "title": "Product Designer",
            "slug": "product-designer",
            "department": "Design",
            "employment_type": "full_time",
            "location": "Berlin",
            "work_mode": "hybrid",
            "skills": ["Figma", "Prototyping"],
            "openings": 1,
            "status": JobStatus.PENDING_APPROVAL,
            "approval_status": JobApprovalStatus.PENDING,
        },
        {
            "title": "Support Specialist",
            "slug": "support-specialist",
            "department": "Support",
            "employment_type": "full_time",
            "location": "Remote",
            "work_mode": "remote",
            "openings": 1,
            "status": JobStatus.CLOSED,
            "approval_status": JobApprovalStatus.APPROVED,
            "published_at": PUBLISHED_AT,
            "closed_at": CLOSED_AT,
        },
    ]


def _get_or_create_jobs(
    session: Session,
    organization: Organization,
    users: dict[str, User],
    summary: SeedSummary,
) -> dict[str, Job]:
    recruiter = users[MembershipRole.RECRUITER.value]
    hiring_manager = users[MembershipRole.HIRING_MANAGER.value]
    for role_user in (recruiter, hiring_manager):
        membership = session.exec(
            select(Membership).where(
                Membership.organization_id == organization.id,
                Membership.user_id == role_user.id,
            )
        ).one_or_none()
        if membership is None:
            raise SeedRefusedError(
                "Seed ordering violated: job assignee has no membership"
            )

    jobs: dict[str, Job] = {}
    for payload in _job_payloads(recruiter.id, hiring_manager.id):
        validated = JobCreate(
            **{
                k: v
                for k, v in payload.items()
                if k not in {"status", "approval_status", "published_at", "closed_at"}
            }
        )
        existing = session.exec(
            select(Job).where(
                Job.organization_id == organization.id,
                Job.slug == validated.slug,
            )
        ).one_or_none()
        if existing is not None:
            summary.skipped.append(f"job:{organization.slug}:{existing.slug}")
            jobs[existing.slug] = existing
            continue
        job = Job(
            **validated.model_dump(),
            id=_seed_id("job", organization.slug, validated.slug),
            organization_id=organization.id,
            status=payload["status"],
            approval_status=payload["approval_status"],
            published_at=payload.get("published_at"),
            closed_at=payload.get("closed_at"),
        )
        session.add(job)
        summary.created_jobs += 1
        jobs[job.slug] = job
    return jobs


def _get_or_create_applications(
    session: Session,
    organization: Organization,
    jobs: dict[str, Job],
    summary: SeedSummary,
) -> None:
    for email, first_name, last_name, job_slug, stage in SEED_CANDIDATES:
        normalized_email = _email_adapter.validate_python(email).lower()
        candidate = session.exec(
            select(Candidate).where(
                Candidate.organization_id == organization.id,
                Candidate.email == normalized_email,
            )
        ).one_or_none()
        if candidate is None:
            candidate = Candidate(
                id=_seed_id("candidate", organization.slug, normalized_email),
                organization_id=organization.id,
                first_name=first_name,
                last_name=last_name,
                email=normalized_email,
                location="Berlin",
                current_title="Software Engineer",
                skills=["Python", "SQL"],
                source="seed",
                consent_status=True,
            )
            session.add(candidate)
            summary.created_candidates += 1
        else:
            summary.skipped.append(f"candidate:{organization.slug}:{candidate.email}")

        job = jobs[job_slug]
        application = session.exec(
            select(Application).where(
                Application.organization_id == organization.id,
                Application.candidate_id == candidate.id,
                Application.job_id == job.id,
            )
        ).one_or_none()
        if application is None:
            application = Application(
                id=_seed_id(
                    "application", organization.slug, normalized_email, job_slug
                ),
                organization_id=organization.id,
                candidate_id=candidate.id,
                job_id=job.id,
                stage=stage,
                source="seed",
                cover_letter="Seeded development application.",
            )
            session.add(application)
            summary.created_applications += 1
        else:
            summary.skipped.append(f"application:{organization.slug}:{job_slug}")

        resume = session.exec(
            select(ResumeDocument).where(
                ResumeDocument.organization_id == organization.id,
                ResumeDocument.application_id == application.id,
            )
        ).one_or_none()
        if resume is None:
            resume_id = _seed_id(
                "resume", organization.slug, normalized_email, job_slug
            )
            session.add(
                ResumeDocument(
                    id=resume_id,
                    organization_id=organization.id,
                    candidate_id=candidate.id,
                    application_id=application.id,
                    storage_key=resume_storage_key(
                        organization_id=organization.id,
                        candidate_id=candidate.id,
                        resume_id=resume_id,
                    ),
                    original_filename="resume.pdf",
                    content_type="application/pdf",
                    size_bytes=SEED_RESUME_SIZE_BYTES,
                    status=ResumeProcessingStatus.UPLOADED,
                )
            )
            summary.created_resumes += 1
        else:
            summary.skipped.append(f"resume:{organization.slug}:{job_slug}")


def seed_development_data(session: Session) -> SeedSummary:
    """Create the deterministic development dataset in an existing schema."""
    _require_seeding_allowed()
    summary = SeedSummary()
    organization = _get_or_create_organization(session, summary)
    users = _get_or_create_users(session, organization, summary)
    jobs = _get_or_create_jobs(session, organization, users, summary)
    _get_or_create_applications(session, organization, jobs, summary)
    session.commit()
    logger.info(
        "development seed complete organization=%s users=%d memberships=%d "
        "jobs=%d candidates=%d applications=%d resumes=%d skipped=%d",
        organization.slug,
        summary.created_users,
        summary.created_memberships,
        summary.created_jobs,
        summary.created_candidates,
        summary.created_applications,
        summary.created_resumes,
        len(summary.skipped),
    )
    return summary


def main() -> None:
    """Entry point: ``uv run python -m app.db.seed`` from ``apps/api``."""
    _require_seeding_allowed()
    with Session(engine) as session:
        summary = seed_development_data(session)
    print(
        "Seeded development data: "
        f"{summary.created_organizations} organizations, "
        f"{summary.created_users} users, "
        f"{summary.created_memberships} memberships, "
        f"{summary.created_jobs} jobs, "
        f"{summary.created_candidates} candidates, "
        f"{summary.created_applications} applications, "
        f"{summary.created_resumes} resumes "
        f"({len(summary.skipped)} already present)."
    )


if __name__ == "__main__":
    main()


__all__ = [
    "SEED_DEV_PASSWORD",
    "SEED_ORG_SLUG",
    "SeedRefusedError",
    "SeedSummary",
    "seed_development_data",
]
