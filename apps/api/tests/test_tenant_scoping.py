"""Tenant-scoped query pattern tests.

The helpers in ``app/db/tenant.py`` are exercised against ``Membership`` --
a real tenant-owned table -- so no fake production entities are needed and
no production schema changes are involved. These tests prove the PRD
invariant at the query layer: an object ID alone never resolves another
tenant's row, because the tenant predicate is part of the SQL itself.
"""

from uuid import uuid4

from app.core.security import create_access_token
from app.db.tenant import (
    delete_for_org,
    exists_for_org,
    get_by_id_for_org,
    list_for_org,
    scoped_select,
    update_for_org,
)
from app.models import Membership, MembershipRole, Organization, User


def _make_user(session, email, n):
    user = User(
        email=email,
        first_name="Tenant",
        last_name=f"User{n}",
        is_email_verified=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _make_org(session, name):
    org = Organization(name=name)
    session.add(org)
    session.commit()
    session.refresh(org)
    return org


def _make_membership(session, org, user, role=MembershipRole.RECRUITER):
    membership = Membership(organization_id=org.id, user_id=user.id, role=role)
    session.add(membership)
    session.commit()
    session.refresh(membership)
    return membership


def _two_tenants(session):
    """Two orgs, each with one member; returns (org_a, org_b, member_a, member_b)."""
    org_a = _make_org(session, "Org A")
    org_b = _make_org(session, "Org B")
    user_a = _make_user(session, "a@hireflow.test", 1)
    user_b = _make_user(session, "b@hireflow.test", 2)
    member_a = _make_membership(session, org_a, user_a)
    member_b = _make_membership(session, org_b, user_b)
    return org_a, org_b, member_a, member_b


def _headers(user):
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def test_get_by_id_for_org_returns_own_row(session):
    org_a, _, member_a, _ = _two_tenants(session)

    found = get_by_id_for_org(session, Membership, member_a.id, org_a.id)

    assert found is not None
    assert found.id == member_a.id


def test_get_by_id_for_org_hides_foreign_row(session):
    _, org_b, member_a, _ = _two_tenants(session)

    # The row exists, but under the wrong tenant it behaves as nonexistent.
    assert get_by_id_for_org(session, Membership, member_a.id, org_b.id) is None


def test_foreign_and_missing_lookups_are_indistinguishable(session):
    _, org_b, member_a, _ = _two_tenants(session)

    assert get_by_id_for_org(session, Membership, member_a.id, org_b.id) is None
    assert get_by_id_for_org(session, Membership, uuid4(), org_b.id) is None


def test_list_for_org_returns_only_own_rows_and_paginates(session):
    org_a, org_b, _, _ = _two_tenants(session)

    ids_a = {m.id for m in list_for_org(session, Membership, org_a.id)}
    ids_b = {m.id for m in list_for_org(session, Membership, org_b.id)}

    assert len(ids_a) == 1
    assert len(ids_b) == 1
    assert ids_a.isdisjoint(ids_b)

    # Pagination stays tenant-scoped.
    assert len(list_for_org(session, Membership, org_a.id, limit=1, offset=0)) == 1
    assert list_for_org(session, Membership, org_a.id, limit=1, offset=1) == []


def test_exists_for_org(session):
    org_a, org_b, member_a, _ = _two_tenants(session)

    assert exists_for_org(session, Membership, member_a.id, org_a.id) is True
    assert exists_for_org(session, Membership, member_a.id, org_b.id) is False
    assert exists_for_org(session, Membership, uuid4(), org_a.id) is False


def test_update_for_org_applies_only_within_tenant(session):
    org_a, org_b, member_a, _ = _two_tenants(session)

    updated = update_for_org(
        session,
        Membership,
        member_a.id,
        org_a.id,
        {"role": MembershipRole.ADMIN},
    )
    session.commit()
    assert updated is not None
    assert updated.role == MembershipRole.ADMIN

    # Cross-tenant update is a silent miss; the row is untouched.
    assert (
        update_for_org(
            session,
            Membership,
            member_a.id,
            org_b.id,
            {"role": MembershipRole.INTERVIEWER},
        )
        is None
    )
    session.commit()
    session.refresh(member_a)
    assert member_a.role == MembershipRole.ADMIN


def test_delete_for_org_removes_only_within_tenant(session):
    org_a, org_b, member_a, member_b = _two_tenants(session)

    assert delete_for_org(session, Membership, member_a.id, org_b.id) is False
    session.commit()
    # Foreign row survives the cross-tenant attempt.
    assert get_by_id_for_org(session, Membership, member_a.id, org_a.id) is not None

    assert delete_for_org(session, Membership, member_a.id, org_a.id) is True
    session.commit()
    assert get_by_id_for_org(session, Membership, member_a.id, org_a.id) is None
    # The other tenant is unaffected.
    assert get_by_id_for_org(session, Membership, member_b.id, org_b.id) is not None


def test_helpers_never_commit(session, monkeypatch):
    org_a, _, member_a, _ = _two_tenants(session)

    def _boom():
        raise AssertionError("helper must not commit; caller owns the transaction")

    monkeypatch.setattr(session, "commit", _boom)

    assert get_by_id_for_org(session, Membership, member_a.id, org_a.id) is not None
    assert list_for_org(session, Membership, org_a.id) != []
    assert exists_for_org(session, Membership, member_a.id, org_a.id) is True
    assert (
        update_for_org(
            session, Membership, member_a.id, org_a.id, {"role": member_a.role}
        )
        is not None
    )
    assert delete_for_org(session, Membership, uuid4(), org_a.id) is False


def test_scoped_select_embeds_tenant_predicate_in_sql(session):
    org_a, _, _, _ = _two_tenants(session)

    compiled = scoped_select(Membership, org_a.id).compile(
        compile_kwargs={"literal_binds": True}
    )

    assert "organization_id" in str(compiled)
    assert org_a.id.hex in str(compiled).replace("-", "")


def test_endpoint_list_uses_context_org_not_spoofed_id(client, session):
    """A caller admin in both tenants sees each tenant's own rows only."""
    org_a = _make_org(session, "Org A")
    org_b = _make_org(session, "Org B")
    admin = _make_user(session, "boss@hireflow.test", 9)
    _make_membership(session, org_a, admin, MembershipRole.ADMIN)
    _make_membership(session, org_b, admin, MembershipRole.ADMIN)
    other = _make_user(session, "peer@hireflow.test", 10)
    _make_membership(session, org_b, other, MembershipRole.RECRUITER)

    rows_a = client.get(
        f"/api/v1/organizations/{org_a.id}/memberships", headers=_headers(admin)
    )
    rows_b = client.get(
        f"/api/v1/organizations/{org_b.id}/memberships", headers=_headers(admin)
    )

    assert rows_a.status_code == 200
    assert rows_b.status_code == 200
    assert [m["user"]["email"] for m in rows_a.json()] == ["boss@hireflow.test"]
    assert sorted(m["user"]["email"] for m in rows_b.json()) == [
        "boss@hireflow.test",
        "peer@hireflow.test",
    ]


def test_spoofed_query_param_cannot_override_context_scope(client, session):
    """Extra client-supplied org IDs are ignored; ctx drives the query."""
    org_a = _make_org(session, "Org A")
    org_b = _make_org(session, "Org B")
    admin = _make_user(session, "boss@hireflow.test", 9)
    _make_membership(session, org_a, admin, MembershipRole.ADMIN)
    peer = _make_user(session, "peer@hireflow.test", 10)
    _make_membership(session, org_b, peer, MembershipRole.RECRUITER)

    response = client.get(
        f"/api/v1/organizations/{org_a.id}/memberships",
        params={"organization_id": str(org_b.id)},
        headers=_headers(admin),
    )

    assert response.status_code == 200
    assert [m["user"]["email"] for m in response.json()] == ["boss@hireflow.test"]
