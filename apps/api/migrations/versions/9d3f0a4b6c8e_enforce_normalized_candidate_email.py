"""enforce normalized candidate email

Revision ID: 9d3f0a4b6c8e
Revises: 8c2e9f3a5b7d
Create Date: 2026-09-28 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "9d3f0a4b6c8e"
down_revision: str | Sequence[str] | None = "8c2e9f3a5b7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_candidates_email_normalized", "candidates", "email = lower(email)"
    )


def downgrade() -> None:
    op.drop_constraint("ck_candidates_email_normalized", "candidates", type_="check")
