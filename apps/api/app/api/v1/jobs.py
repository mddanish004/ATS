from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from app.api.deps import OrganizationContext, require_permission
from app.core.permissions import Permission
from app.db.database import get_session
from app.db.tenant import get_by_id_for_org, scoped_select, update_for_org
from app.models import Job, JobApprovalStatus, JobStatus, Membership
from app.schemas import JobCreate, JobRead, JobUpdate

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Missing, malformed, or expired access token",
        },
        status.HTTP_403_FORBIDDEN: {
            "description": "Email verification required or insufficient permissions",
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Job not found",
        },
    },
)


def _job_not_found() -> HTTPException:
    # Same response for "no such job" and "another tenant's job": a valid
    # object ID must never confirm another tenant's object existence.
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Job not found",
    )


def _require_org_member(
    session: Session,
    organization_id: UUID,
    user_id: UUID,
    field_name: str,
) -> None:
    """Assignees must be existing members of the current organization.

    One message for "no such user" and "user outside this organization" so
    the check reveals neither user existence nor foreign memberships.
    """
    member = session.exec(
        select(Membership).where(
            Membership.organization_id == organization_id,
            Membership.user_id == user_id,
        )
    ).first()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} must reference a member of the organization",
        )


def _require_slug_available(
    session: Session,
    organization_id: UUID,
    slug: str,
    exclude_job_id: UUID | None = None,
) -> None:
    conflict = session.exec(
        select(Job.id).where(
            Job.organization_id == organization_id,
            Job.slug == slug,
        )
    ).first()
    if conflict is not None and conflict != exclude_job_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job slug already exists in this organization",
        )


def _transition_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Job is not in a state that allows this operation",
    )


def _transition_job(
    session: Session,
    job_id: UUID,
    ctx: OrganizationContext,
    allowed: set[JobStatus],
    new_status: JobStatus,
    approval_status: JobApprovalStatus | None = None,
) -> Job:
    job = get_by_id_for_org(session, Job, job_id, ctx.organization.id)
    if job is None:
        raise _job_not_found()
    if job.status not in allowed:
        raise _transition_conflict()
    job.status = new_status
    if approval_status is not None:
        job.approval_status = approval_status
    job.updated_at = datetime.now(UTC)
    session.add(job)
    # Future AuditEvent recording can be added here in the same transaction.
    session.commit()
    session.refresh(job)
    return job


@router.post(
    "",
    response_model=JobRead,
    status_code=status.HTTP_201_CREATED,
)
def create_job(
    payload: JobCreate,
    organization_id: UUID,
    ctx: Annotated[
        OrganizationContext, Depends(require_permission(Permission.JOBS_CREATE))
    ],
    session: Annotated[Session, Depends(get_session)],
) -> Job:
    # Organization comes from the resolved membership context; any
    # client-supplied organization identifier only selects that context and
    # was already validated against the caller's memberships.
    resolved_org_id = ctx.organization.id
    _require_slug_available(session, resolved_org_id, payload.slug)
    if payload.hiring_manager_id is not None:
        _require_org_member(
            session, resolved_org_id, payload.hiring_manager_id, "hiring_manager_id"
        )
    if payload.recruiter_id is not None:
        _require_org_member(
            session, resolved_org_id, payload.recruiter_id, "recruiter_id"
        )

    job = Job(**payload.model_dump(), organization_id=resolved_org_id)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@router.get(
    "/{job_id}",
    response_model=JobRead,
)
def get_job(
    job_id: UUID,
    organization_id: UUID,
    ctx: Annotated[
        OrganizationContext, Depends(require_permission(Permission.JOBS_READ))
    ],
    session: Annotated[Session, Depends(get_session)],
) -> Job:
    job = get_by_id_for_org(session, Job, job_id, ctx.organization.id)
    if job is None:
        raise _job_not_found()
    return job


@router.get(
    "",
    response_model=list[JobRead],
)
def list_jobs(
    organization_id: UUID,
    ctx: Annotated[
        OrganizationContext, Depends(require_permission(Permission.JOBS_READ))
    ],
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Job]:
    rows = session.exec(
        scoped_select(Job, ctx.organization.id)
        .order_by(Job.created_at, Job.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows)


@router.patch(
    "/{job_id}",
    response_model=JobRead,
)
def update_job(
    job_id: UUID,
    payload: JobUpdate,
    organization_id: UUID,
    ctx: Annotated[
        OrganizationContext, Depends(require_permission(Permission.JOBS_UPDATE))
    ],
    session: Annotated[Session, Depends(get_session)],
) -> Job:
    # JobUpdate carries no id/organization_id fields, so ownership cannot be
    # moved through the payload; only explicitly set fields are applied.
    changes = payload.changes()
    job = get_by_id_for_org(session, Job, job_id, ctx.organization.id)
    if job is None:
        raise _job_not_found()
    if job.status in {JobStatus.CLOSED, JobStatus.ARCHIVED}:
        raise _transition_conflict()
    if "slug" in changes:
        _require_slug_available(
            session, ctx.organization.id, changes["slug"], exclude_job_id=job_id
        )
    if changes.get("hiring_manager_id") is not None:
        _require_org_member(
            session,
            ctx.organization.id,
            changes["hiring_manager_id"],
            "hiring_manager_id",
        )
    if changes.get("recruiter_id") is not None:
        _require_org_member(
            session, ctx.organization.id, changes["recruiter_id"], "recruiter_id"
        )

    job = update_for_org(session, Job, job_id, ctx.organization.id, changes)
    if job is None:
        raise _job_not_found()
    job.updated_at = datetime.now(UTC)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@router.post("/{job_id}/submit-for-approval", response_model=JobRead)
def submit_job_for_approval(
    job_id: UUID,
    organization_id: UUID,
    ctx: Annotated[
        OrganizationContext, Depends(require_permission(Permission.JOBS_UPDATE))
    ],
    session: Annotated[Session, Depends(get_session)],
) -> Job:
    return _transition_job(
        session,
        job_id,
        ctx,
        {JobStatus.DRAFT},
        JobStatus.PENDING_APPROVAL,
        JobApprovalStatus.PENDING,
    )


@router.post("/{job_id}/approve", response_model=JobRead)
def approve_job(
    job_id: UUID,
    organization_id: UUID,
    ctx: Annotated[
        OrganizationContext, Depends(require_permission(Permission.JOBS_APPROVE))
    ],
    session: Annotated[Session, Depends(get_session)],
) -> Job:
    return _transition_job(
        session,
        job_id,
        ctx,
        {JobStatus.PENDING_APPROVAL},
        JobStatus.APPROVED,
        JobApprovalStatus.APPROVED,
    )


@router.post("/{job_id}/reject", response_model=JobRead)
def reject_job(
    job_id: UUID,
    organization_id: UUID,
    ctx: Annotated[
        OrganizationContext, Depends(require_permission(Permission.JOBS_APPROVE))
    ],
    session: Annotated[Session, Depends(get_session)],
) -> Job:
    # Rejection returns the job to editable DRAFT while retaining the outcome
    # in approval_status; a subsequent submission starts a new pending review.
    return _transition_job(
        session,
        job_id,
        ctx,
        {JobStatus.PENDING_APPROVAL},
        JobStatus.DRAFT,
        JobApprovalStatus.REJECTED,
    )


@router.post("/{job_id}/publish", response_model=JobRead)
def publish_job(
    job_id: UUID,
    organization_id: UUID,
    ctx: Annotated[
        OrganizationContext, Depends(require_permission(Permission.JOBS_PUBLISH))
    ],
    session: Annotated[Session, Depends(get_session)],
) -> Job:
    job = get_by_id_for_org(session, Job, job_id, ctx.organization.id)
    if job is None:
        raise _job_not_found()
    if (
        job.status != JobStatus.APPROVED
        or job.approval_status != JobApprovalStatus.APPROVED
    ):
        raise _transition_conflict()
    job.status = JobStatus.PUBLISHED
    job.published_at = datetime.now(UTC)
    job.updated_at = job.published_at
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@router.post("/{job_id}/hold", response_model=JobRead)
def hold_job(
    job_id: UUID,
    organization_id: UUID,
    ctx: Annotated[
        OrganizationContext, Depends(require_permission(Permission.JOBS_UPDATE))
    ],
    session: Annotated[Session, Depends(get_session)],
) -> Job:
    return _transition_job(
        session, job_id, ctx, {JobStatus.PUBLISHED}, JobStatus.ON_HOLD
    )


@router.post("/{job_id}/resume", response_model=JobRead)
def resume_job(
    job_id: UUID,
    organization_id: UUID,
    ctx: Annotated[
        OrganizationContext, Depends(require_permission(Permission.JOBS_UPDATE))
    ],
    session: Annotated[Session, Depends(get_session)],
) -> Job:
    return _transition_job(
        session, job_id, ctx, {JobStatus.ON_HOLD}, JobStatus.PUBLISHED
    )


@router.post("/{job_id}/close", response_model=JobRead)
def close_job(
    job_id: UUID,
    organization_id: UUID,
    ctx: Annotated[
        OrganizationContext, Depends(require_permission(Permission.JOBS_UPDATE))
    ],
    session: Annotated[Session, Depends(get_session)],
) -> Job:
    job = get_by_id_for_org(session, Job, job_id, ctx.organization.id)
    if job is None:
        raise _job_not_found()
    if job.status not in {JobStatus.PUBLISHED, JobStatus.ON_HOLD}:
        raise _transition_conflict()
    now = datetime.now(UTC)
    job.status = JobStatus.CLOSED
    job.closed_at = now
    job.updated_at = now
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@router.post("/{job_id}/archive", response_model=JobRead)
def archive_job(
    job_id: UUID,
    organization_id: UUID,
    ctx: Annotated[
        OrganizationContext, Depends(require_permission(Permission.JOBS_UPDATE))
    ],
    session: Annotated[Session, Depends(get_session)],
) -> Job:
    return _transition_job(session, job_id, ctx, {JobStatus.CLOSED}, JobStatus.ARCHIVED)
