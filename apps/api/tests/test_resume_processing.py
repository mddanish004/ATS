from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.models import (
    Application,
    Candidate,
    Job,
    JobApprovalStatus,
    JobStatus,
    Organization,
    ResumeDocument,
    ResumeProcessingStatus,
)
from app.services.resume_processing import (
    PermanentResumeProcessingError,
    ResumeProcessingOutcome,
    TransientResumeProcessingError,
    mark_resume_queued,
    process_resume_document,
    resume_processing_retry_delay,
)
from app.services.resume_queue import (
    ArqResumeProcessingQueue,
    redis_settings_from_url,
    resume_processing_task_id,
)
from app.workers import resume_worker
from app.workers.resume_worker import WorkerSettings
from arq import Retry
from sqlmodel import select


def _resume(session, *, status=ResumeProcessingStatus.UPLOADED):
    organization = Organization(name=f"Acme {uuid4()}", slug=f"acme-{uuid4().hex}")
    other_organization = Organization(
        name=f"Globex {uuid4()}", slug=f"globex-{uuid4().hex}"
    )
    session.add(organization)
    session.add(other_organization)
    session.commit()

    candidate = Candidate(
        organization_id=organization.id,
        first_name="Casey",
        last_name="Candidate",
        email=f"casey-{uuid4().hex}@example.com",
    )
    job = Job(
        organization_id=organization.id,
        title="Backend Engineer",
        slug=f"engineer-{uuid4().hex}",
        status=JobStatus.PUBLISHED,
        approval_status=JobApprovalStatus.APPROVED,
        published_at=datetime.now(UTC),
    )
    session.add(candidate)
    session.add(job)
    session.commit()

    application = Application(
        organization_id=organization.id,
        candidate_id=candidate.id,
        job_id=job.id,
    )
    resume = ResumeDocument(
        organization_id=organization.id,
        candidate_id=candidate.id,
        application_id=application.id,
        storage_key=f"org/{organization.id}/candidates/{candidate.id}/resumes/r.pdf",
        original_filename="resume.pdf",
        content_type="application/pdf",
        size_bytes=42,
        status=status,
    )
    session.add(application)
    session.add(resume)
    session.commit()
    session.refresh(resume)
    return organization, other_organization, resume


def test_uploaded_resume_transitions_to_queued(session):
    organization, _, resume = _resume(session)

    queued = mark_resume_queued(
        session,
        organization_id=organization.id,
        resume_document_id=resume.id,
    )
    session.commit()

    assert queued.status == ResumeProcessingStatus.QUEUED


def test_worker_processing_reaches_needs_review_without_parsing(session):
    organization, _, resume = _resume(session, status=ResumeProcessingStatus.QUEUED)

    status = process_resume_document(
        session,
        task_id="task",
        organization_id=organization.id,
        resume_document_id=resume.id,
    )

    stored = session.exec(select(ResumeDocument)).one()
    assert status == ResumeProcessingStatus.NEEDS_REVIEW
    assert stored.status == ResumeProcessingStatus.NEEDS_REVIEW
    assert stored.error_message == "Resume parsing is not implemented yet"


def test_processing_can_complete_when_processor_has_meaningful_work(session):
    class CompletingProcessor:
        def process(self, resume):
            return ResumeProcessingOutcome(status=ResumeProcessingStatus.COMPLETED)

    organization, _, resume = _resume(session, status=ResumeProcessingStatus.QUEUED)

    status = process_resume_document(
        session,
        task_id="task",
        organization_id=organization.id,
        resume_document_id=resume.id,
        processor=CompletingProcessor(),
    )

    assert status == ResumeProcessingStatus.COMPLETED


def test_permanent_processing_failure_reaches_failed(session):
    class FailingProcessor:
        def process(self, resume):
            raise PermanentResumeProcessingError("invalid pdf")

    organization, _, resume = _resume(session, status=ResumeProcessingStatus.QUEUED)

    status = process_resume_document(
        session,
        task_id="task",
        organization_id=organization.id,
        resume_document_id=resume.id,
        processor=FailingProcessor(),
    )

    stored = session.get(ResumeDocument, resume.id)
    assert status == ResumeProcessingStatus.FAILED
    assert stored.status == ResumeProcessingStatus.FAILED
    assert stored.error_message == "invalid pdf"


def test_transient_failure_retries_until_limit(session, monkeypatch):
    class TransientProcessor:
        def process(self, resume):
            raise TransientResumeProcessingError("temporary outage")

    monkeypatch.setattr("app.core.config.settings.resume_processing_max_retries", 2)
    organization, _, resume = _resume(session, status=ResumeProcessingStatus.QUEUED)

    with pytest.raises(TransientResumeProcessingError):
        process_resume_document(
            session,
            task_id="task",
            organization_id=organization.id,
            resume_document_id=resume.id,
            attempt=1,
            processor=TransientProcessor(),
        )

    stored = session.get(ResumeDocument, resume.id)
    assert stored.status == ResumeProcessingStatus.PROCESSING
    assert stored.error_message == "temporary outage"

    status = process_resume_document(
        session,
        task_id="task",
        organization_id=organization.id,
        resume_document_id=resume.id,
        attempt=2,
        processor=TransientProcessor(),
    )

    stored = session.get(ResumeDocument, resume.id)
    assert status == ResumeProcessingStatus.FAILED
    assert stored.status == ResumeProcessingStatus.FAILED
    assert stored.error_message == "temporary outage"


def test_retry_delay_is_exponential_and_capped(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.resume_processing_retry_base_seconds", 5
    )
    monkeypatch.setattr(
        "app.core.config.settings.resume_processing_retry_max_seconds", 12
    )

    assert resume_processing_retry_delay(1) == 5
    assert resume_processing_retry_delay(2) == 10
    assert resume_processing_retry_delay(3) == 12


def test_duplicate_terminal_task_is_idempotent(session):
    organization, _, resume = _resume(session, status=ResumeProcessingStatus.COMPLETED)

    status = process_resume_document(
        session,
        task_id="task",
        organization_id=organization.id,
        resume_document_id=resume.id,
    )

    assert status == ResumeProcessingStatus.COMPLETED
    assert (
        session.get(ResumeDocument, resume.id).status
        == ResumeProcessingStatus.COMPLETED
    )


def test_nonexistent_resume_is_handled_safely(session):
    status = process_resume_document(
        session,
        task_id="task",
        organization_id=uuid4(),
        resume_document_id=uuid4(),
    )

    assert status is None


def test_cross_tenant_resume_is_not_processed(session):
    _, other_organization, resume = _resume(
        session, status=ResumeProcessingStatus.QUEUED
    )

    status = process_resume_document(
        session,
        task_id="task",
        organization_id=other_organization.id,
        resume_document_id=resume.id,
    )

    assert status is None
    assert (
        session.get(ResumeDocument, resume.id).status == ResumeProcessingStatus.QUEUED
    )


def test_worker_configuration_registers_resume_task():
    names = {function.name for function in WorkerSettings.functions}

    assert "process_resume" in names
    assert WorkerSettings.queue_name


@pytest.mark.anyio
async def test_worker_executes_resume_task_with_tenant_context(monkeypatch):
    calls = []

    class FakeSession:
        def __init__(self, engine):
            self.engine = engine

        def __enter__(self):
            return "session"

        def __exit__(self, exc_type, exc, traceback):
            return False

    def process_resume_document(session, **kwargs):
        kwargs["session"] = session
        calls.append(kwargs)

    monkeypatch.setattr(resume_worker, "Session", FakeSession)
    monkeypatch.setattr(
        resume_worker,
        "process_resume_document",
        process_resume_document,
    )
    organization_id = uuid4()
    resume_id = uuid4()

    await resume_worker.process_resume(
        {"job_try": 2},
        "task-id",
        str(organization_id),
        str(resume_id),
    )

    assert calls == [
        {
            "session": "session",
            "task_id": "task-id",
            "organization_id": organization_id,
            "resume_document_id": resume_id,
            "attempt": 2,
        }
    ]


@pytest.mark.anyio
async def test_worker_converts_transient_error_to_arq_retry(monkeypatch):
    class FakeSession:
        def __init__(self, engine):
            self.engine = engine

        def __enter__(self):
            return "session"

        def __exit__(self, exc_type, exc, traceback):
            return False

    def process_resume_document(session, **kwargs):
        raise TransientResumeProcessingError("temporary outage")

    monkeypatch.setattr(resume_worker, "Session", FakeSession)
    monkeypatch.setattr(
        resume_worker,
        "process_resume_document",
        process_resume_document,
    )

    with pytest.raises(Retry):
        await resume_worker.process_resume(
            {"job_try": 1},
            "task-id",
            str(uuid4()),
            str(uuid4()),
        )


def test_redis_settings_load_from_url():
    redis_settings = redis_settings_from_url("redis://:secret@localhost:6380/2")

    assert redis_settings.host == "localhost"
    assert redis_settings.port == 6380
    assert redis_settings.database == 2
    assert redis_settings.password == "secret"


@pytest.mark.anyio
async def test_arq_queue_enqueues_deterministic_task(monkeypatch):
    calls = []

    class FakeRedis:
        async def enqueue_job(self, *args, **kwargs):
            calls.append((args, kwargs))

        async def close(self):
            calls.append(("close",))

    async def create_pool(settings):
        calls.append(("pool", settings.host, settings.database))
        return FakeRedis()

    monkeypatch.setattr("app.services.resume_queue.create_pool", create_pool)
    organization_id = uuid4()
    resume_id = uuid4()
    queue = ArqResumeProcessingQueue(
        redis_url="redis://localhost:6379/0",
        queue_name="queue",
    )

    task_id = await queue.enqueue_resume_processing(
        organization_id=organization_id,
        resume_document_id=resume_id,
    )

    assert task_id == resume_processing_task_id(
        organization_id=organization_id,
        resume_document_id=resume_id,
    )
    assert calls[1] == (
        (
            "process_resume",
            task_id,
            str(organization_id),
            str(resume_id),
        ),
        {"_job_id": task_id, "_queue_name": "queue"},
    )
