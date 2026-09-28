from datetime import UTC, datetime

import pytest

from app.models import Job, JobApprovalStatus, JobStatus, Organization


def _organization(session, name: str, slug: str) -> Organization:
    organization = Organization(name=name, slug=slug)
    session.add(organization)
    session.commit()
    session.refresh(organization)
    return organization


def _job(
    session,
    organization: Organization,
    *,
    slug: str,
    status: JobStatus,
) -> Job:
    job = Job(
        organization_id=organization.id,
        title=f"{status.value.replace('_', ' ').title()} Engineer",
        slug=slug,
        department="Engineering",
        employment_type="full_time",
        location="Remote",
        work_mode="remote",
        salary_min=100_000,
        salary_max=150_000,
        currency="USD",
        description="Build reliable hiring software.",
        responsibilities="Ship production features.",
        requirements="Python experience.",
        preferred_qualifications="ATS experience.",
        skills=["Python", "SQL"],
        openings=2,
        approval_status=JobApprovalStatus.APPROVED,
        status=status,
        published_at=datetime.now(UTC) if status == JobStatus.PUBLISHED else None,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@pytest.mark.parametrize(
    "hidden_status",
    [
        JobStatus.DRAFT,
        JobStatus.PENDING_APPROVAL,
        JobStatus.APPROVED,
        JobStatus.ON_HOLD,
        JobStatus.CLOSED,
        JobStatus.ARCHIVED,
    ],
)
def test_public_list_only_returns_published_jobs(client, session, hidden_status):
    organization = _organization(session, "Acme", "acme")
    published = _job(
        session, organization, slug="published-job", status=JobStatus.PUBLISHED
    )
    _job(session, organization, slug="hidden-job", status=hidden_status)

    response = client.get("/api/v1/acme/jobs")

    assert response.status_code == 200
    assert [job["slug"] for job in response.json()] == [published.slug]


def test_public_job_detail_is_unauthenticated_and_candidate_safe(client, session):
    organization = _organization(session, "Acme", "acme")
    _job(session, organization, slug="backend-engineer", status=JobStatus.PUBLISHED)

    response = client.get("/api/v1/acme/jobs/backend-engineer")

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "slug": "backend-engineer",
        "title": "Published Engineer",
        "department": "Engineering",
        "employment_type": "full_time",
        "location": "Remote",
        "work_mode": "remote",
        "salary_min": 100_000,
        "salary_max": 150_000,
        "currency": "USD",
        "description": "Build reliable hiring software.",
        "responsibilities": "Ship production features.",
        "requirements": "Python experience.",
        "preferred_qualifications": "ATS experience.",
        "skills": ["Python", "SQL"],
        "openings": 2,
        "target_hire_date": None,
        "published_at": data["published_at"],
    }
    assert data["published_at"] is not None
    assert not {
        "id",
        "organization_id",
        "hiring_manager_id",
        "recruiter_id",
        "status",
        "approval_status",
        "closed_at",
        "created_at",
        "updated_at",
    }.intersection(data)


def test_non_public_job_detail_returns_safe_not_found(client, session):
    organization = _organization(session, "Acme", "acme")
    _job(session, organization, slug="secret-job", status=JobStatus.APPROVED)

    response = client.get("/api/v1/acme/jobs/secret-job")

    assert response.status_code == 404
    assert response.json() == {"detail": "Public careers resource not found"}


def test_wrong_organization_or_job_slug_returns_not_found(client, session):
    organization = _organization(session, "Acme", "acme")
    _organization(session, "Other", "other")
    _job(session, organization, slug="backend-engineer", status=JobStatus.PUBLISHED)

    assert client.get("/api/v1/other/jobs/backend-engineer").status_code == 404
    assert client.get("/api/v1/acme/jobs/not-a-job").status_code == 404
    assert client.get("/api/v1/unknown/jobs/backend-engineer").status_code == 404


def test_same_job_slug_resolves_within_each_organization(client, session):
    acme = _organization(session, "Acme", "acme")
    globex = _organization(session, "Globex", "globex")
    acme_job = _job(session, acme, slug="backend-engineer", status=JobStatus.PUBLISHED)
    globex_job = _job(
        session, globex, slug="backend-engineer", status=JobStatus.PUBLISHED
    )
    acme_job.title = "Acme Backend Engineer"
    globex_job.title = "Globex Backend Engineer"
    session.add(acme_job)
    session.add(globex_job)
    session.commit()

    assert (
        client.get("/api/v1/acme/jobs/backend-engineer").json()["title"]
        == "Acme Backend Engineer"
    )
    assert (
        client.get("/api/v1/globex/jobs/backend-engineer").json()["title"]
        == "Globex Backend Engineer"
    )


def test_public_list_supports_search_filters_and_pagination(client, session):
    organization = _organization(session, "Acme", "acme")
    matching = _job(
        session, organization, slug="backend-engineer", status=JobStatus.PUBLISHED
    )
    other = _job(
        session, organization, slug="other-engineer", status=JobStatus.PUBLISHED
    )
    other.department = "Sales"
    other.location = "London"
    session.add(other)
    session.commit()

    response = client.get(
        "/api/v1/acme/jobs",
        params={"search": "backend", "department": "engineering", "limit": 1},
    )

    assert response.status_code == 200
    assert [job["slug"] for job in response.json()] == [matching.slug]
