"""add resume error message

Revision ID: a4e7c2d9f1b3
Revises: 9d3f0a4b6c8e
Create Date: 2026-09-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4e7c2d9f1b3"
down_revision: str | Sequence[str] | None = "9d3f0a4b6c8e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "resume_documents",
        sa.Column("error_message", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("resume_documents", "error_message")
