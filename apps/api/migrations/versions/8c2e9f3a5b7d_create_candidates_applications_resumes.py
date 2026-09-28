"""create candidates applications and resume documents

Revision ID: 8c2e9f3a5b7d
Revises: 7b1d8e2f4a6c
Create Date: 2026-09-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c2e9f3a5b7d"
down_revision: str | Sequence[str] | None = "7b1d8e2f4a6c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    application_stage = sa.Enum(
        "applied",
        "screening",
        "shortlisted",
        "interview",
        "assessment",
        "offer",
        "hired",
        "rejected",
        "withdrawn",
        name="application_stage",
    )
    resume_processing_status = sa.Enum(
        "uploaded",
        "queued",
        "processing",
        "completed",
        "failed",
        "needs_review",
        name="resume_processing_status",
    )

    op.create_table(
        "candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("first_name", sa.String(), nullable=False),
        sa.Column("last_name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("linkedin_url", sa.String(), nullable=True),
        sa.Column("github_url", sa.String(), nullable=True),
        sa.Column("portfolio_url", sa.String(), nullable=True),
        sa.Column("current_title", sa.String(), nullable=True),
        sa.Column("current_company", sa.String(), nullable=True),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column("years_of_experience", sa.Float(), nullable=True),
        sa.Column("education", sa.JSON(), nullable=False),
        sa.Column("certifications", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("consent_status", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "years_of_experience IS NULL OR years_of_experience >= 0",
            name="ck_candidates_years_experience_nonnegative",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "email", name="uq_candidates_organization_email"
        ),
    )
    op.create_index(
        "ix_candidates_organization_created",
        "candidates",
        ["organization_id", "created_at"],
    )
    op.create_index(
        op.f("ix_candidates_organization_id"),
        "candidates",
        ["organization_id"],
    )

    op.create_table(
        "applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("stage", application_stage, nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("cover_letter", sa.String(), nullable=True),
        sa.Column("screening_answers", sa.JSON(), nullable=False),
        sa.Column("applied_at", sa.DateTime(), nullable=False),
        sa.Column("last_stage_change_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "candidate_id",
            "job_id",
            name="uq_applications_organization_candidate_job",
        ),
    )
    op.create_index(
        op.f("ix_applications_candidate_id"),
        "applications",
        ["candidate_id"],
    )
    op.create_index(op.f("ix_applications_job_id"), "applications", ["job_id"])
    op.create_index(
        "ix_applications_organization_job_stage",
        "applications",
        ["organization_id", "job_id", "stage"],
    )
    op.create_index(
        op.f("ix_applications_organization_id"),
        "applications",
        ["organization_id"],
    )

    op.create_table(
        "resume_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", resume_processing_status, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(
        op.f("ix_resume_documents_application_id"),
        "resume_documents",
        ["application_id"],
    )
    op.create_index(
        op.f("ix_resume_documents_candidate_id"),
        "resume_documents",
        ["candidate_id"],
    )
    op.create_index(
        op.f("ix_resume_documents_organization_id"),
        "resume_documents",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_resume_documents_organization_id"),
        table_name="resume_documents",
    )
    op.drop_index(
        op.f("ix_resume_documents_candidate_id"), table_name="resume_documents"
    )
    op.drop_index(
        op.f("ix_resume_documents_application_id"), table_name="resume_documents"
    )
    op.drop_table("resume_documents")
    op.drop_index(op.f("ix_applications_organization_id"), table_name="applications")
    op.drop_index("ix_applications_organization_job_stage", table_name="applications")
    op.drop_index(op.f("ix_applications_job_id"), table_name="applications")
    op.drop_index(op.f("ix_applications_candidate_id"), table_name="applications")
    op.drop_table("applications")
    op.drop_index(op.f("ix_candidates_organization_id"), table_name="candidates")
    op.drop_index("ix_candidates_organization_created", table_name="candidates")
    op.drop_table("candidates")
    sa.Enum(name="resume_processing_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="application_stage").drop(op.get_bind(), checkfirst=True)
