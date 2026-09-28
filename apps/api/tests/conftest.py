import os
from collections.abc import Generator
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault(
    "JWT_SECRET_KEY", "test-secret-key-0123456789abcdef0123456789abcdef"
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.security import create_access_token
from app.db.database import get_session
from app.main import app
from app.models import Organization, User
from app.services.resume_queue import get_resume_processing_queue
from app.services.storage_service import LocalPrivateStorage, get_private_storage


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def session(engine) -> Generator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture()
def private_storage(tmp_path):
    return LocalPrivateStorage(tmp_path / "private-uploads")


@pytest.fixture()
def resume_processing_queue():
    class InMemoryResumeProcessingQueue:
        def __init__(self):
            self.jobs = []

        async def enqueue_resume_processing(
            self,
            *,
            organization_id,
            resume_document_id,
        ) -> str:
            task_id = f"resume-processing:{organization_id}:{resume_document_id}"
            self.jobs.append((organization_id, resume_document_id, task_id))
            return task_id

    return InMemoryResumeProcessingQueue()


@pytest.fixture()
def client(session, private_storage, resume_processing_queue) -> Generator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_private_storage] = lambda: private_storage
    app.dependency_overrides[get_resume_processing_queue] = (
        lambda: resume_processing_queue
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def user(session) -> User:
    user = User(
        email="admin@hireflow.test",
        first_name="Ada",
        last_name="Admin",
        is_email_verified=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture()
def auth_headers(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def organization(session) -> Organization:
    organization = Organization(name="Acme Inc")
    session.add(organization)
    session.commit()
    session.refresh(organization)
    return organization


@pytest.fixture()
def missing_organization_id():
    return str(uuid4())
