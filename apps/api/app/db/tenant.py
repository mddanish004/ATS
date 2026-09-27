"""Tenant-scoped query helpers.

Every tenant-owned row carries ``organization_id``. These helpers put the
tenant predicate inside the SQL itself -- never as a post-fetch Python
check -- so a bare resource ID can never resolve another tenant's object::

    WHERE id = :resource_id AND organization_id = :organization_id

Caller contract (routers/services):
- accept ``ctx: OrganizationContext`` from ``get_organization_context`` (or
  ``require_permission(...)``), which is resolved server-side from the
  authenticated user's membership;
- pass ``ctx.organization.id`` as ``organization_id`` -- never an ID taken
  from the request body, query params, or frontend state;
- translate ``None`` / ``False`` / ``[]`` into the endpoint's normal
  404/empty response, so a cross-tenant miss is indistinguishable from a
  nonexistent row (see the PRD invariant: an object ID alone must never be
  enough to retrieve another tenant's object).

These helpers never commit; the caller owns the transaction, matching the
existing router convention (mutate, then ``session.commit()``).

Future tenant-owned model example (e.g. jobs)::

    class Job(SQLModel, table=True):
        id: UUID = Field(default_factory=uuid4, primary_key=True)
        organization_id: UUID = Field(foreign_key="organizations.id", index=True)
        ...

    @router.get("/jobs/{job_id}", response_model=JobRead)
    def get_job(
        job_id: UUID,
        ctx: Annotated[OrganizationContext, Depends(require_permission("jobs.read"))],
        session: Annotated[Session, Depends(get_session)],
    ) -> Job:
        job = get_by_id_for_org(session, Job, job_id, ctx.organization.id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job
"""

from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.sql.selectable import Select
from sqlmodel import Session, SQLModel, select


class TenantScoped(Protocol):
    """Structural shape of a tenant-owned table."""

    id: Any
    organization_id: UUID


def scoped_select[T: TenantScoped](
    model: type[T], organization_id: UUID, *extra: Any
) -> Select:
    """Start a ``select(model)`` already constrained to one tenant.

    Extra entities may be passed for joins, keeping the tenant predicate on
    the base query::

        stmt = (
            scoped_select(Membership, ctx.organization.id, User)
            .join(User, Membership.user_id == User.id)
            .order_by(Membership.created_at)
        )
    """
    return select(model, *extra).where(model.organization_id == organization_id)


def get_by_id_for_org[T: TenantScoped](
    session: Session,
    model: type[T],
    resource_id: UUID,
    organization_id: UUID,
) -> T | None:
    """Fetch one row by ID *within* a tenant; ``None`` if absent or foreign."""
    return session.exec(
        select(model).where(
            model.id == resource_id,
            model.organization_id == organization_id,
        )
    ).first()


def list_for_org[T: TenantScoped](
    session: Session,
    model: type[T],
    organization_id: UUID,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[T]:
    """List rows belonging to one tenant (paginated, bounded)."""
    return list(
        session.exec(
            scoped_select(model, organization_id).limit(limit).offset(offset)
        ).all()
    )


def exists_for_org[T: TenantScoped](
    session: Session,
    model: type[T],
    resource_id: UUID,
    organization_id: UUID,
) -> bool:
    """True only if the row exists *within* the tenant."""
    return (
        session.exec(
            select(model.id).where(
                model.id == resource_id,
                model.organization_id == organization_id,
            )
        ).first()
        is not None
    )


def update_for_org[T: TenantScoped](
    session: Session,
    model: type[T],
    resource_id: UUID,
    organization_id: UUID,
    values: dict[str, Any],
) -> T | None:
    """Apply ``values`` to a tenant-owned row; ``None`` if absent or foreign.

    Stages the change on the session without committing -- the caller
    commits, so multi-step updates stay atomic.
    """
    obj = get_by_id_for_org(session, model, resource_id, organization_id)
    if obj is None:
        return None
    if isinstance(obj, SQLModel):
        for key, value in values.items():
            setattr(obj, key, value)
        session.add(obj)
    return obj


def delete_for_org[T: TenantScoped](
    session: Session,
    model: type[T],
    resource_id: UUID,
    organization_id: UUID,
) -> bool:
    """Delete a tenant-owned row; ``False`` if absent or foreign.

    Stages the deletion without committing -- the caller commits.
    """
    obj = get_by_id_for_org(session, model, resource_id, organization_id)
    if obj is None:
        return False
    session.delete(obj)
    return True
