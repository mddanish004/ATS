from typing import Annotated, Any

from pydantic import AnyHttpUrl, ConfigDict, EmailStr, Field, StringConstraints
from sqlmodel import SQLModel

NameField = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
ShortField = Annotated[
    str | None, StringConstraints(strip_whitespace=True, max_length=255)
]


class PublicApplicationCreate(SQLModel):
    model_config = ConfigDict(extra="forbid")

    first_name: NameField
    last_name: NameField
    email: EmailStr
    phone: ShortField = None
    location: ShortField = None
    linkedin_url: AnyHttpUrl | None = None
    github_url: AnyHttpUrl | None = None
    portfolio_url: AnyHttpUrl | None = None
    current_title: ShortField = None
    current_company: ShortField = None
    skills: list[str] = Field(default_factory=list, max_length=100)
    years_of_experience: float | None = Field(default=None, ge=0, le=100)
    education: list[str] = Field(default_factory=list, max_length=100)
    certifications: list[str] = Field(default_factory=list, max_length=100)
    source: ShortField = None
    consent_status: bool = False
    cover_letter: Annotated[
        str | None, StringConstraints(strip_whitespace=True, max_length=20_000)
    ] = None
    screening_answers: dict[str, Any] = Field(default_factory=dict)


class ApplicationSubmissionRead(SQLModel):
    message: str
