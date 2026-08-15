"""Seed the default company and user.

Phase 1 has no authentication, so every dataset is attributed to this pair.
The UUIDs are literals, not generated, so the seed is reproducible and matches
the defaults in app.core.config.

The inserts go through typed ``sa.Uuid`` columns rather than raw SQL text:
SQLAlchemy stores UUIDs as native uuid on PostgreSQL but as dash-less CHAR(32)
on other dialects, so a hand-written dash-formatted literal would never match
the value the ORM later queries with.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from app.core.config import DEFAULT_COMPANY_ID, DEFAULT_USER_ID, settings

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

companies = sa.table(
    "companies",
    sa.column("id", sa.Uuid()),
    sa.column("name", sa.String()),
    sa.column("slug", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

users = sa.table(
    "users",
    sa.column("id", sa.Uuid()),
    sa.column("company_id", sa.Uuid()),
    sa.column("email", sa.String()),
    sa.column("display_name", sa.String()),
    sa.column("is_active", sa.Boolean()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)

    exists = bind.execute(
        sa.select(companies.c.id).where(companies.c.id == DEFAULT_COMPANY_ID)
    ).first()
    if exists is None:
        op.bulk_insert(
            companies,
            [
                {
                    "id": DEFAULT_COMPANY_ID,
                    "name": settings.default_company_name,
                    "slug": "default",
                    "created_at": now,
                    "updated_at": now,
                }
            ],
        )

    exists = bind.execute(sa.select(users.c.id).where(users.c.id == DEFAULT_USER_ID)).first()
    if exists is None:
        op.bulk_insert(
            users,
            [
                {
                    "id": DEFAULT_USER_ID,
                    "company_id": DEFAULT_COMPANY_ID,
                    "email": settings.default_user_email,
                    "display_name": settings.default_user_name,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
            ],
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(users.delete().where(users.c.id == DEFAULT_USER_ID))
    bind.execute(companies.delete().where(companies.c.id == DEFAULT_COMPANY_ID))
