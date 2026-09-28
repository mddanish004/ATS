import sys
import types
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models import (
    Application,
    Candidate,
    Job,
    JobApprovalStatus,
    JobStatus,
    Organization,
    ResumeDocument,
)
from app.services.resume_service import (
    authorized_resume_signed_url,
    resume_storage_key,
)
from app.services.storage_service import LocalPrivateStorage, R2PrivateStorage


def _resume_record(session):
    organization = Organization(name="Acme", slug=f"acme-{uuid4().hex}")
    other_organization = Organization(name="Globex", slug=f"globex-{uuid4().hex}")
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
    resume_id = uuid4()
    resume = ResumeDocument(
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
        size_bytes=42,
    )
    session.add(application)
    session.add(resume)
    session.commit()
    return organization, other_organization, resume


def test_local_private_storage_upload_download_sign_and_delete(tmp_path):
    storage = LocalPrivateStorage(tmp_path / "private")
    key = "org/org-id/candidates/candidate-id/resumes/resume-id/original.pdf"

    storage.upload_object(
        key,
        b"private",
        content_type="application/pdf",
        metadata={"organization_id": "org-id"},
    )

    assert storage.download_object(key) == b"private"
    assert storage.generate_signed_url(key, expires_in=60).startswith(
        f"private://local/{key}?expires="
    )
    storage.delete(key)
    assert not (storage.root / key).exists()


def test_resume_storage_key_is_tenant_scoped():
    organization_id = uuid4()
    candidate_id = uuid4()
    resume_id = uuid4()

    key = resume_storage_key(
        organization_id=organization_id,
        candidate_id=candidate_id,
        resume_id=resume_id,
    )

    assert key == (
        f"org/{organization_id}/candidates/{candidate_id}/resumes/"
        f"{resume_id}/original.pdf"
    )


def test_signed_url_requires_matching_organization(session, tmp_path):
    organization, other_organization, resume = _resume_record(session)
    storage = LocalPrivateStorage(tmp_path / "private")
    storage.upload_object(resume.storage_key, b"pdf", content_type="application/pdf")

    url = authorized_resume_signed_url(
        session=session,
        storage=storage,
        organization_id=organization.id,
        resume_id=resume.id,
    )

    assert url.startswith("private://local/")
    with pytest.raises(HTTPException) as exc_info:
        authorized_resume_signed_url(
            session=session,
            storage=storage,
            organization_id=other_organization.id,
            resume_id=resume.id,
        )
    assert exc_info.value.status_code == 404


def test_r2_storage_uses_s3_compatible_private_operations(monkeypatch):
    calls = []

    class Body:
        def read(self):
            return b"downloaded"

    class Client:
        def put_object(self, **kwargs):
            calls.append(("put", kwargs))

        def get_object(self, **kwargs):
            calls.append(("get", kwargs))
            return {"Body": Body()}

        def delete_object(self, **kwargs):
            calls.append(("delete", kwargs))

        def generate_presigned_url(self, operation, Params, ExpiresIn):
            calls.append(("sign", operation, Params, ExpiresIn))
            return "https://signed.example.test/private"

    def client(service, **kwargs):
        calls.append(("client", service, kwargs))
        return Client()

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=client))
    monkeypatch.setitem(
        sys.modules,
        "botocore.config",
        types.SimpleNamespace(Config=lambda **kwargs: kwargs),
    )

    storage = R2PrivateStorage(
        endpoint_url="https://account.r2.cloudflarestorage.com",
        bucket_name="resumes",
        access_key_id="access",
        secret_access_key="secret",
        region_name="auto",
    )

    storage.upload_object(
        "org/o/candidates/c/resumes/r/original.pdf",
        b"pdf",
        content_type="application/pdf",
        metadata={"organization_id": "o"},
    )
    assert storage.download_object("org/o/candidates/c/resumes/r/original.pdf") == (
        b"downloaded"
    )
    storage.delete("org/o/candidates/c/resumes/r/original.pdf")
    assert (
        storage.generate_signed_url(
            "org/o/candidates/c/resumes/r/original.pdf",
            expires_in=300,
        )
        == "https://signed.example.test/private"
    )

    assert calls[0][0:2] == ("client", "s3")
    assert calls[1] == (
        "put",
        {
            "Bucket": "resumes",
            "Key": "org/o/candidates/c/resumes/r/original.pdf",
            "Body": b"pdf",
            "ContentType": "application/pdf",
            "Metadata": {"organization_id": "o"},
        },
    )
    assert calls[2] == (
        "get",
        {"Bucket": "resumes", "Key": "org/o/candidates/c/resumes/r/original.pdf"},
    )
    assert calls[3] == (
        "delete",
        {"Bucket": "resumes", "Key": "org/o/candidates/c/resumes/r/original.pdf"},
    )
    assert calls[4] == (
        "sign",
        "get_object",
        {"Bucket": "resumes", "Key": "org/o/candidates/c/resumes/r/original.pdf"},
        300,
    )
