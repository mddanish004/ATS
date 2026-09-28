"""Model- and schema-level tests for the Job requisition entity (Day 5).

No CRUD API exists yet; these tests pin the model defaults, constraints,
relationships, tenant ownership, and request-schema validation that the
future Jobs API will build on.
"""

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.db.tenant import get_by_id_for_org
from app.models import Job, Organization, User
from app.models.job import EmploymentType, JobApprovalStatus, JobStatus, WorkMode
from app.schemas import JobCreate, JobRead, JobUpdate


def _make_org(session, name="Acme Inc"):
    org = Organization(name=name)
    session.add(org)
    session.commit()
    session.refresh(org)
    return org


def _make_user(session, email):
    user = User(email=email, first_name="Ada", last_name="Lovelace")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _make_job(session, org, **overrides):
    values = {
        "organization_id": org.id,
        "title": "Backend Engineer",
        "slug": "backend-engineer",
    }
    values.update(overrides)
    job = Job(**values)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_valid_job_creation_with_defaults(session):
    org = _make_org(session)

    job = _make_job(session, org)

    assert job.id
    assert job.organization_id == org.id
    assert job.title == "Backend Engineer"
    assert job.status == JobStatus.DRAFT
    assert job.approval_status == JobApprovalStatus.NONE
    assert job.openings == 1
    assert job.skills == []
    assert job.hiring_manager_id is None
    assert job.recruiter_id is None
    assert job.published_at is None
    assert job.closed_at is None
    assert job.created_at is not None
    assert job.updated_at is not None


def test_model_defaults_use_timezone_aware_timestamps():
    job = Job(organization_id=uuid4(), title="T", slug="t")

    assert job.created_at.tzinfo is not None
    assert job.updated_at.tzinfo is not None


def test_valid_job_creation_with_all_fields(session):
    org = _make_org(session)
    hm = _make_user(session, "hm@example.com")
    rec = _make_user(session, "recruiter@example.com")

    job = _make_job(
        session,
        org,
        title="Senior Backend Engineer",
        slug="senior-backend-engineer",
        department="Engineering",
        employment_type=EmploymentType.FULL_TIME,
        location="Berlin, DE",
        work_mode=WorkMode.HYBRID,
        salary_min=90000,
        salary_max=120000,
        currency="EUR",
        description="Build hiring tools.",
        responsibilities="Ship APIs.",
        requirements="5y Python.",
        preferred_qualifications="Postgres.",
        skills=["Python", "FastAPI", "PostgreSQL"],
        hiring_manager_id=hm.id,
        recruiter_id=rec.id,
        openings=2,
        target_hire_date=date(2026, 12, 1),
        status=JobStatus.PUBLISHED,
        approval_status=JobApprovalStatus.APPROVED,
    )

    fetched = session.get(Job, job.id)
    assert fetched is not None
    assert fetched.employment_type == EmploymentType.FULL_TIME
    assert fetched.work_mode == WorkMode.HYBRID
    assert fetched.skills == ["Python", "FastAPI", "PostgreSQL"]
    assert fetched.target_hire_date == date(2026, 12, 1)
    assert session.get(User, fetched.hiring_manager_id) is not None
    assert session.get(User, fetched.recruiter_id) is not None


@pytest.mark.parametrize("status", list(JobStatus))
def test_all_lifecycle_statuses_persist(status):
    assert JobStatus(status.value) == status


@pytest.mark.parametrize("status", list(JobApprovalStatus))
def test_all_approval_statuses_persist(status):
    assert JobApprovalStatus(status.value) == status


def test_invalid_status_rejected_by_schema():
    with pytest.raises(ValidationError):
        JobCreate(title="T", slug="t", status="NOPE")  # type: ignore[arg-type]


def test_invalid_approval_status_rejected_by_schema():
    with pytest.raises(ValidationError):
        JobCreate(title="T", slug="t", approval_status="NOPE")  # type: ignore[arg-type]


def test_job_create_requires_title_slug_and_org():
    with pytest.raises(ValidationError):
        JobCreate(title="   ", slug="t")
    with pytest.raises(ValidationError):
        JobCreate(title="T", slug="Not A Slug!!")


def test_job_create_validates_salary_range_and_openings():
    with pytest.raises(ValidationError):
        JobCreate(title="T", slug="t", salary_min=120000, salary_max=90000)
    with pytest.raises(ValidationError):
        JobCreate(title="T", slug="t", openings=0)
    with pytest.raises(ValidationError):
        JobCreate(title="T", slug="t", currency="dollar")


def test_salary_range_check_enforced_by_database(session):
    org = _make_org(session)
    session.add(
        Job(organization_id=org.id, title="T", slug="t", salary_min=5, salary_max=1)
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_slug_unique_per_organization(session):
    org_a = _make_org(session, "Org A")
    org_b = _make_org(session, "Org B")
    _make_job(session, org_a, slug="backend-engineer")

    # Same slug in another org is fine.
    other = _make_job(session, org_b, slug="backend-engineer")
    assert other.organization_id == org_b.id

    # Duplicate slug in the same org violates the constraint.
    session.add(Job(organization_id=org_a.id, title="Dup", slug="backend-engineer"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_job_belongs_to_organization(session):
    org = _make_org(session)
    job = _make_job(session, org)
    assert job.organization_id == org.id


def test_tenant_scoped_lookup_cannot_cross_organizations(session):
    org_a = _make_org(session, "Org A")
    org_b = _make_org(session, "Org B")
    job = _make_job(session, org_a)

    assert get_by_id_for_org(session, Job, job.id, org_a.id) is not None
    assert get_by_id_for_org(session, Job, job.id, org_b.id) is None
    assert get_by_id_for_org(session, Job, uuid4(), org_a.id) is None


def test_recruiter_and_hiring_manager_relationships(session):
    org = _make_org(session)
    hm = _make_user(session, "hm@example.com")
    rec = _make_user(session, "recruiter@example.com")
    job = _make_job(session, org, hiring_manager_id=hm.id, recruiter_id=rec.id)

    assert session.get(User, job.hiring_manager_id) == hm
    assert session.get(User, job.recruiter_id) == rec


def test_job_read_schema_round_trip(session):
    org = _make_org(session)
    job = _make_job(session, org, skills=["Go"])

    read = JobRead.model_validate(job)

    assert read.id == job.id
    assert read.organization_id == org.id
    assert read.slug == "backend-engineer"
    assert read.status == JobStatus.DRAFT
    assert read.skills == ["Go"]


def test_job_update_accepts_partial_changes():
    update = JobUpdate(title="New Title")

    assert update.changes() == {"title": "New Title"}
    assert JobUpdate().changes() == {}


def test_job_update_rejects_lifecycle_fields():
    with pytest.raises(ValidationError):
        JobUpdate(status=JobStatus.ON_HOLD)  # type: ignore[call-arg]


def test_job_update_validates_ranges():
    with pytest.raises(ValidationError):
        JobUpdate(salary_min=10, salary_max=5)
    with pytest.raises(ValidationError):
        JobUpdate(openings=0)


def test_jobs_listed_per_tenant(session):
    org_a = _make_org(session, "Org A")
    org_b = _make_org(session, "Org B")
    _make_job(session, org_a, slug="a-1")
    _make_job(session, org_b, slug="b-1")

    rows_a = session.exec(select(Job).where(Job.organization_id == org_a.id)).all()

    assert [j.slug for j in rows_a] == ["a-1"]
