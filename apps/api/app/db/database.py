from sqlmodel import SQLModel, Session, create_engine
from app.core.config import settings
from collections.abc import Generator

engine=create_engine(
    settings.database_url,
    echo=False,
)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session