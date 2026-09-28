from typing import Annotated
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from app.core.config import settings
from app.db.database import get_session
from app.models import (
    Application,
    Candidate,
    EmploymentType,
    Job,
    JobStatus,
    Organization,
    ResumeDocument,
    WorkMode,
)
from app.schemas import (
    ApplicationSubmissionRead,
    PublicApplicationCreate,
    PublicJobRead,
)
from app.services.storage_service import (
    PrivateStorage,
    StorageError,
    get_private_storage,
)

router = APIRouter(
    prefix="/{organization_slug}/jobs",
    tags=["public-careers"],
    responses={status.HTTP_404_NOT_FOUND: {"description": "Public page not found"}},
)


def _not_found() -> HTTPException:
    # Unknown organizations, jobs, and non-public jobs intentionally share one
    # response so workflow state and tenant existence are not disclosed.
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Public careers resource not found",
    )


def _public_organization(session: Session, organization_slug: str) -> Organization:
    organization = session.exec(
        select(Organization).where(Organization.slug == organization_slug)
    ).first()
    if organization is None:
        raise _not_found()
    return organization


@router.get("", response_model=list[PublicJobRead])
def list_public_jobs(
    organization_slug: str,
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    department: str | None = None,
    employment_type: EmploymentType | None = None,
    location: str | None = None,
    work_mode: WorkMode | None = None,
) -> list[Job]:
    organization = _public_organization(session, organization_slug)
    statement = select(Job).where(
        Job.organization_id == organization.id,
        Job.status == JobStatus.PUBLISHED,
    )
    if search is not None:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                Job.title.ilike(pattern),
                Job.slug.ilike(pattern),
                Job.department.ilike(pattern),
                Job.location.ilike(pattern),
            )
        )
    if department is not None:
        statement = statement.where(Job.department.ilike(department.strip()))
    if employment_type is not None:
        statement = statement.where(Job.employment_type == employment_type)
    if location is not None:
        statement = statement.where(Job.location.ilike(location.strip()))
    if work_mode is not None:
        statement = statement.where(Job.work_mode == work_mode)
    rows = session.exec(
        statement.order_by(Job.published_at.desc(), Job.id).limit(limit).offset(offset)
    ).all()
    return list(rows)


@router.get("/{job_slug}", response_model=PublicJobRead)
def get_public_job(
    organization_slug: str,
    job_slug: str,
    session: Annotated[Session, Depends(get_session)],
) -> Job:
    organization = _public_organization(session, organization_slug)
    job = session.exec(
        select(Job).where(
            Job.organization_id == organization.id,
            Job.slug == job_slug,
            Job.status == JobStatus.PUBLISHED,
        )
    ).first()
    if job is None:
        raise _not_found()
    return job


def _application_payload(raw_payload: str) -> PublicApplicationCreate:
    try:
        return PublicApplicationCreate.model_validate_json(raw_payload)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


async def _validated_pdf(resume: UploadFile) -> bytes:
    filename = resume.filename or ""
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Resume must use the .pdf extension",
        )
    if resume.content_type != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Resume must have the application/pdf content type",
        )
    content = await resume.read(settings.max_resume_size_bytes + 1)
    if len(content) > settings.max_resume_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Resume exceeds the configured maximum file size",
        )
    if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-1024:]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Resume content is not a valid PDF",
        )
    return content


@router.post(
    "/{job_slug}/apply",
    response_model=ApplicationSubmissionRead,
    status_code=status.HTTP_201_CREATED,
)
async def submit_public_application(
    organization_slug: str,
    job_slug: str,
    payload: Annotated[str, Form()],
    resume: Annotated[UploadFile, File()],
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[PrivateStorage, Depends(get_private_storage)],
) -> ApplicationSubmissionRead:
    organization = _public_organization(session, organization_slug)
    job = session.exec(
        select(Job).where(
            Job.organization_id == organization.id,
            Job.slug == job_slug,
            Job.status == JobStatus.PUBLISHED,
        )
    ).first()
    if job is None:
        raise _not_found()

    application_data = _application_payload(payload)
    resume_content = await _validated_pdf(resume)
    normalized_email = str(application_data.email).strip().lower()
    candidate = session.exec(
        select(Candidate).where(
            Candidate.organization_id == organization.id,
            Candidate.email == normalized_email,
        )
    ).first()
    if candidate is not None:
        duplicate = session.exec(
            select(Application.id).where(
                Application.organization_id == organization.id,
                Application.candidate_id == candidate.id,
                Application.job_id == job.id,
            )
        ).first()
        if duplicate is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An application has already been submitted for this job",
            )
    else:
        candidate = Candidate(
            organization_id=organization.id,
            first_name=application_data.first_name,
            last_name=application_data.last_name,
            email=normalized_email,
            phone=application_data.phone,
            location=application_data.location,
            linkedin_url=(
                str(application_data.linkedin_url)
                if application_data.linkedin_url is not None
                else None
            ),
            github_url=(
                str(application_data.github_url)
                if application_data.github_url is not None
                else None
            ),
            portfolio_url=(
                str(application_data.portfolio_url)
                if application_data.portfolio_url is not None
                else None
            ),
            current_title=application_data.current_title,
            current_company=application_data.current_company,
            skills=application_data.skills,
            years_of_experience=application_data.years_of_experience,
            education=application_data.education,
            certifications=application_data.certifications,
            source=application_data.source,
            consent_status=application_data.consent_status,
        )
        session.add(candidate)

    application = Application(
        organization_id=organization.id,
        candidate_id=candidate.id,
        job_id=job.id,
        source=application_data.source,
        cover_letter=application_data.cover_letter,
        screening_answers=application_data.screening_answers,
    )
    resume_document_id = uuid4()
    resume_document = ResumeDocument(
        id=resume_document_id,
        organization_id=organization.id,
        candidate_id=candidate.id,
        application_id=application.id,
        storage_key=(
            f"org/{organization.id}/candidates/{candidate.id}/resumes/"
            f"{resume_document_id}/original.pdf"
        ),
        original_filename=(resume.filename or "resume.pdf")
        .replace("\\", "/")
        .rsplit("/", 1)[-1][:255],
        content_type="application/pdf",
        size_bytes=len(resume_content),
    )
    session.add(application)
    session.add(resume_document)

    stored = False
    try:
        session.flush()
        storage.put(resume_document.storage_key, resume_content)
        stored = True
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if stored:
            storage.delete(resume_document.storage_key)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An application has already been submitted for this job",
        ) from exc
    except (StorageError, SQLAlchemyError) as exc:
        session.rollback()
        if stored:
            storage.delete(resume_document.storage_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Application could not be submitted",
        ) from exc

    return ApplicationSubmissionRead(message="Application received")
