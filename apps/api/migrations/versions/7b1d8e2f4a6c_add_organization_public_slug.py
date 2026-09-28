"""add organization public slug

Revision ID: 7b1d8e2f4a6c
Revises: 56dc77c3cd97
Create Date: 2026-09-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b1d8e2f4a6c"
down_revision: str | Sequence[str] | None = "56dc77c3cd97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("slug", sa.String(), nullable=True))
    op.execute(
        """
        UPDATE organizations
        SET slug = COALESCE(
            NULLIF(
                trim(BOTH '-' FROM regexp_replace(lower(name), '[^a-z0-9]+', '-', 'g')),
                ''
            ),
            'organization'
        ) || '-' || left(replace(id::text, '-', ''), 8)
        """
    )
    op.alter_column("organizations", "slug", nullable=False)
    op.create_unique_constraint("uq_organizations_slug", "organizations", ["slug"])


def downgrade() -> None:
    op.drop_constraint("uq_organizations_slug", "organizations", type_="unique")
    op.drop_column("organizations", "slug")
