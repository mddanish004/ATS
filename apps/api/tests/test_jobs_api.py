"""API/integration tests for the authenticated Job CRUD endpoints."""

from uuid import UUID, uuid4

from sqlmodel import select

from app.core.security import create_access_token
from app.models import Job, Membership, MembershipRole, Organization, User

BASE = "/api/v1/jobs"


def _make_user(session, email):
    user = User(
        email=email,
        first_name="Test",
        last_name="User",
        is_email_verified=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _headers(user):
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _member(session, organization, user, role):
    session.add(
        Membership(
            organization_id=organization.id,
            user_id=user.id,
            role=role,
        )
    )
    session.commit()


def _make_org(session, name):
    org = Organization(name=name)
    session.add(org)
    session.commit()
    session.refresh(org)
    return org


def _job_payload(**overrides):
    payload = {"title": "Backend Engineer", "slug": "backend-engineer"}
    payload.update(overrides)
    return payload


def _setup_org_with_admin(session):
    """Organization + admin member + headers; returns (org, admin, headers)."""
    org = _make_org(session, "Acme Inc")
    admin = _make_user(session, "admin@hireflow.test")
    _member(session, org, admin, MembershipRole.ADMIN)
    return org, admin, _headers(admin)


def _create_job(client, headers, org_id, **overrides):
    return client.post(
        BASE,
        params={"organization_id": str(org_id)},
        headers=headers,
        json=_job_payload(**overrides),
    )


def test_authorized_user_can_create_job(client, session):
    org, _, headers = _setup_org_with_admin(session)

    response = _create_job(client, headers, org.id)

    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Backend Engineer"
    assert data["organization_id"] == str(org.id)
    assert data["status"] == "draft"
    assert "password_hash" not in data


def test_user_without_create_permission_receives_403(client, session):
    org = _make_org(session, "Acme Inc")
    viewer = _make_user(session, "viewer@hireflow.test")
    _member(session, org, viewer, MembershipRole.INTERVIEWER)

    response = _create_job(client, _headers(viewer), org.id)

    assert response.status_code == 403
    assert session.exec(select(Job)).all() == []


def test_created_job_belongs_to_authenticated_organization(client, session):
    org, _, headers = _setup_org_with_admin(session)

    job_id = _create_job(client, headers, org.id).json()["id"]

    job = session.get(Job, UUID(job_id))
    assert job is not None
    assert job.organization_id == org.id


def test_user_can_retrieve_own_organization_job(client, session):
    org, _, headers = _setup_org_with_admin(session)
    job_id = _create_job(client, headers, org.id).json()["id"]

    response = client.get(
        f"{BASE}/{job_id}",
        params={"organization_id": str(org.id)},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["id"] == job_id


def test_user_cannot_retrieve_another_organization_job(client, session):
    org_a, _, headers_a = _setup_org_with_admin(session)
    org_b = _make_org(session, "Other Inc")
    other_admin = _make_user(session, "other@hireflow.test")
    _member(session, org_b, other_admin, MembershipRole.ADMIN)
    job_id = _create_job(client, _headers(other_admin), org_b.id).json()["id"]

    response = client.get(
        f"{BASE}/{job_id}",
        params={"organization_id": str(org_a.id)},
        headers=headers_a,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


def test_user_lists_only_own_organization_jobs(client, session):
    org_a, _, headers_a = _setup_org_with_admin(session)
    org_b = _make_org(session, "Other Inc")
    other_admin = _make_user(session, "other@hireflow.test")
    _member(session, org_b, other_admin, MembershipRole.ADMIN)
    _create_job(client, headers_a, org_a.id, slug="job-a")
    _create_job(client, _headers(other_admin), org_b.id, slug="job-b")

    response = client.get(
        BASE, params={"organization_id": str(org_a.id)}, headers=headers_a
    )

    assert response.status_code == 200
    assert [j["slug"] for j in response.json()] == ["job-a"]


def test_cannot_create_job_in_another_organization(client, session):
    _, _, headers_a = _setup_org_with_admin(session)
    org_b = _make_org(session, "Other Inc")

    response = _create_job(client, headers_a, org_b.id)

    assert response.status_code == 404
    assert session.exec(select(Job)).all() == []


def test_cannot_assign_recruiter_from_another_organization(client, session):
    org, _, headers = _setup_org_with_admin(session)
    outsider = _make_user(session, "outsider@hireflow.test")

    response = _create_job(client, headers, org.id, recruiter_id=str(outsider.id))

    assert response.status_code == 422
    assert session.exec(select(Job)).all() == []


def test_cannot_assign_hiring_manager_from_another_organization(client, session):
    org, _, headers = _setup_org_with_admin(session)
    outsider = _make_user(session, "outsider@hireflow.test")

    response = _create_job(client, headers, org.id, hiring_manager_id=str(outsider.id))

    assert response.status_code == 422
    assert session.exec(select(Job)).all() == []


def test_can_assign_organization_members(client, session):
    org, _, headers = _setup_org_with_admin(session)
    hm = _make_user(session, "hm@hireflow.test")
    rec = _make_user(session, "rec@hireflow.test")
    _member(session, org, hm, MembershipRole.HIRING_MANAGER)
    _member(session, org, rec, MembershipRole.RECRUITER)

    response = _create_job(
        client,
        headers,
        org.id,
        hiring_manager_id=str(hm.id),
        recruiter_id=str(rec.id),
    )

    assert response.status_code == 201
    assert response.json()["hiring_manager_id"] == str(hm.id)
    assert response.json()["recruiter_id"] == str(rec.id)


def test_authorized_user_can_update_own_job(client, session):
    org, _, headers = _setup_org_with_admin(session)
    job_id = _create_job(client, headers, org.id).json()["id"]

    response = client.patch(
        f"{BASE}/{job_id}",
        params={"organization_id": str(org.id)},
        headers=headers,
        json={"title": "Senior Backend Engineer", "openings": 3},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Senior Backend Engineer"
    assert response.json()["openings"] == 3
    assert response.json()["slug"] == "backend-engineer"


def test_user_without_update_permission_receives_403(client, session):
    org, _, headers_admin = _setup_org_with_admin(session)
    viewer = _make_user(session, "viewer@hireflow.test")
    _member(session, org, viewer, MembershipRole.INTERVIEWER)
    job_id = _create_job(client, headers_admin, org.id).json()["id"]

    response = client.patch(
        f"{BASE}/{job_id}",
        params={"organization_id": str(org.id)},
        headers=_headers(viewer),
        json={"title": "Hacked"},
    )

    assert response.status_code == 403
    assert session.get(Job, UUID(job_id)).title == "Backend Engineer"


def test_user_cannot_update_another_organization_job(client, session):
    org_a, _, headers_a = _setup_org_with_admin(session)
    org_b = _make_org(session, "Other Inc")
    other_admin = _make_user(session, "other@hireflow.test")
    _member(session, org_b, other_admin, MembershipRole.ADMIN)
    job_id = _create_job(client, _headers(other_admin), org_b.id).json()["id"]

    response = client.patch(
        f"{BASE}/{job_id}",
        params={"organization_id": str(org_a.id)},
        headers=headers_a,
        json={"title": "Hacked"},
    )

    assert response.status_code == 404
    assert session.get(Job, UUID(job_id)).title == "Backend Engineer"


def test_organization_ownership_cannot_change_via_update(client, session):
    org, _, headers = _setup_org_with_admin(session)
    other_org = _make_org(session, "Other Inc")
    job_id = _create_job(client, headers, org.id).json()["id"]

    response = client.patch(
        f"{BASE}/{job_id}",
        params={"organization_id": str(org.id)},
        headers=headers,
        json={"title": "Renamed", "organization_id": str(other_org.id)},
    )

    assert response.status_code == 200
    assert response.json()["organization_id"] == str(org.id)
    assert session.get(Job, UUID(job_id)).organization_id == org.id


def test_duplicate_slug_conflicts(client, session):
    org, _, headers = _setup_org_with_admin(session)
    _create_job(client, headers, org.id)

    response = _create_job(client, headers, org.id)

    assert response.status_code == 409


def test_pagination_behaves_correctly(client, session):
    org, _, headers = _setup_org_with_admin(session)
    for i in range(5):
        _create_job(client, headers, org.id, slug=f"job-{i}")

    page1 = client.get(
        BASE,
        params={"organization_id": str(org.id), "limit": 2, "offset": 0},
        headers=headers,
    )
    page2 = client.get(
        BASE,
        params={"organization_id": str(org.id), "limit": 2, "offset": 2},
        headers=headers,
    )
    page3 = client.get(
        BASE,
        params={"organization_id": str(org.id), "limit": 2, "offset": 4},
        headers=headers,
    )

    assert [j["slug"] for j in page1.json()] == ["job-0", "job-1"]
    assert [j["slug"] for j in page2.json()] == ["job-2", "job-3"]
    assert [j["slug"] for j in page3.json()] == ["job-4"]

    bad = client.get(
        BASE,
        params={"organization_id": str(org.id), "limit": 101},
        headers=headers,
    )
    assert bad.status_code == 422


def test_unauthenticated_requests_rejected(client, session):
    org = _make_org(session, "Acme Inc")
    params = {"organization_id": str(org.id)}

    assert client.post(BASE, params=params, json=_job_payload()).status_code == 401
    assert client.get(f"{BASE}/{uuid4()}", params=params).status_code == 401
    assert client.get(BASE, params=params).status_code == 401
    assert (
        client.patch(f"{BASE}/{uuid4()}", params=params, json={"title": "X"})
    ).status_code == 401


def _action(client, headers, org_id, job_id, action):
    return client.post(
        f"{BASE}/{job_id}/{action}",
        params={"organization_id": str(org_id)},
        headers=headers,
    )


def _approved_job(client, headers, org_id):
    job_id = _create_job(client, headers, org_id).json()["id"]
    assert (
        _action(client, headers, org_id, job_id, "submit-for-approval").status_code
        == 200
    )
    return job_id


def test_job_lifecycle_happy_path_and_timestamps(client, session):
    org, _, headers = _setup_org_with_admin(session)
    job_id = _create_job(client, headers, org.id).json()["id"]

    submitted = _action(client, headers, org.id, job_id, "submit-for-approval")
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "pending_approval"
    assert submitted.json()["approval_status"] == "pending"

    approved = _action(client, headers, org.id, job_id, "approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["approval_status"] == "approved"

    published = _action(client, headers, org.id, job_id, "publish")
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["published_at"] is not None

    assert (
        _action(client, headers, org.id, job_id, "hold").json()["status"] == "on_hold"
    )
    resumed = _action(client, headers, org.id, job_id, "resume")
    assert resumed.json()["status"] == "published"
    closed = _action(client, headers, org.id, job_id, "close")
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    assert closed.json()["closed_at"] is not None
    archived = _action(client, headers, org.id, job_id, "archive")
    assert archived.json()["status"] == "archived"
    assert session.get(Job, UUID(job_id)).published_at is not None
    assert session.get(Job, UUID(job_id)).closed_at is not None


def test_pending_approval_can_be_rejected_and_resubmitted(client, session):
    org, _, headers = _setup_org_with_admin(session)
    job_id = _approved_job(client, headers, org.id)
    rejected = _action(client, headers, org.id, job_id, "reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "draft"
    assert rejected.json()["approval_status"] == "rejected"
    resubmitted = _action(client, headers, org.id, job_id, "submit-for-approval")
    assert resubmitted.json()["approval_status"] == "pending"


def test_invalid_lifecycle_transitions_conflict(client, session):
    org, _, headers = _setup_org_with_admin(session)
    job_id = _create_job(client, headers, org.id).json()["id"]
    for action in ("approve", "publish", "hold", "resume", "close", "archive"):
        assert _action(client, headers, org.id, job_id, action).status_code == 409


def test_approval_and_publish_permissions_are_enforced(client, session):
    org = _make_org(session, "Permissions Inc")
    recruiter = _make_user(session, "workflow-recruiter@hireflow.test")
    manager = _make_user(session, "workflow-manager@hireflow.test")
    _member(session, org, recruiter, MembershipRole.RECRUITER)
    _member(session, org, manager, MembershipRole.HIRING_MANAGER)
    recruiter_headers = _headers(recruiter)
    manager_headers = _headers(manager)
    job_id = _approved_job(client, recruiter_headers, org.id)

    assert (
        _action(client, recruiter_headers, org.id, job_id, "approve").status_code == 403
    )
    assert (
        _action(client, manager_headers, org.id, job_id, "approve").status_code == 200
    )
    assert (
        _action(client, manager_headers, org.id, job_id, "publish").status_code == 403
    )
    assert (
        _action(client, recruiter_headers, org.id, job_id, "publish").status_code == 200
    )


def test_lifecycle_is_tenant_scoped_and_generic_update_cannot_bypass(client, session):
    org_a, _, headers_a = _setup_org_with_admin(session)
    org_b = _make_org(session, "Tenant B")
    admin_b = _make_user(session, "tenant-b-admin@hireflow.test")
    _member(session, org_b, admin_b, MembershipRole.ADMIN)
    headers_b = _headers(admin_b)
    job_id = _create_job(client, headers_b, org_b.id).json()["id"]

    cross_tenant = _action(client, headers_a, org_a.id, job_id, "submit-for-approval")
    assert cross_tenant.status_code == 404
    forged = client.patch(
        f"{BASE}/{job_id}",
        params={"organization_id": str(org_b.id)},
        headers=headers_b,
        json={"status": "published", "approval_status": "approved"},
    )
    assert forged.status_code == 422
    assert session.get(Job, UUID(job_id)).status.value == "draft"


def test_archived_job_cannot_be_updated_or_transitioned(client, session):
    org, _, headers = _setup_org_with_admin(session)
    job_id = _create_job(client, headers, org.id).json()["id"]
    for action in ("submit-for-approval",):
        assert _action(client, headers, org.id, job_id, action).status_code == 200
    assert _action(client, headers, org.id, job_id, "approve").status_code == 200
    assert _action(client, headers, org.id, job_id, "publish").status_code == 200
    assert _action(client, headers, org.id, job_id, "close").status_code == 200
    assert _action(client, headers, org.id, job_id, "archive").status_code == 200
    update = client.patch(
        f"{BASE}/{job_id}",
        params={"organization_id": str(org.id)},
        headers=headers,
        json={"title": "Changed"},
    )
    assert update.status_code == 409
    assert _action(client, headers, org.id, job_id, "publish").status_code == 409
