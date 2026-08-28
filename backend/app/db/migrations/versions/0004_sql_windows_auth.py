"""Windows authentication for saved SQL Server connections.

Adds ``auth_mode`` and makes ``password_encrypted`` nullable, because under
Windows integrated authentication there is no credential to store: the connection
borrows the identity of the process the backend runs as.

Hand-written like its predecessors. The two CHECK constraints are generated from
``SqlAuthMode`` so the migration and the model cannot drift.

Batch mode is used for the column alteration so the same migration runs on SQLite,
which cannot ALTER a column in place. Production is PostgreSQL, but the schema is
meant to stay applicable to both - migration 0001 established that.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.models.enums import SqlAuthMode

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AUTH_MODE_VALID = "auth_mode IN (" + ", ".join(f"'{m.value}'" for m in SqlAuthMode) + ")"

# SQL auth always carries a token; Windows auth never does. Enforced here as well
# as in the schema, because a row that breaks it fails at connect time with a
# driver error rather than a validation one.
_PASSWORD_MATCHES_AUTH_MODE = (
    "(auth_mode = 'windows' AND password_encrypted IS NULL) "
    "OR (auth_mode = 'sql' AND password_encrypted IS NOT NULL)"
)


def upgrade() -> None:
    # Defaulted server-side so existing rows become 'sql' - which is what they
    # are - without a separate UPDATE.
    op.add_column(
        "sql_connections",
        sa.Column(
            "auth_mode",
            sa.String(20),
            nullable=False,
            server_default=SqlAuthMode.SQL.value,
        ),
    )
    with op.batch_alter_table("sql_connections") as batch:
        batch.alter_column(
            "password_encrypted",
            existing_type=sa.Text(),
            nullable=True,
        )
        # Windows auth names no user: the login is the process's own identity.
        batch.alter_column(
            "username",
            existing_type=sa.String(128),
            nullable=False,
            server_default="",
        )
        batch.create_check_constraint("auth_mode_valid", _AUTH_MODE_VALID)
        batch.create_check_constraint(
            "password_matches_auth_mode", _PASSWORD_MATCHES_AUTH_MODE
        )


def downgrade() -> None:
    # Windows-auth rows have no password and cannot satisfy the old NOT NULL, so
    # they are removed rather than given a fabricated ciphertext that would fail
    # to decrypt on first use.
    op.execute("DELETE FROM sql_connections WHERE auth_mode = 'windows'")
    with op.batch_alter_table("sql_connections") as batch:
        # Bare names, not the ck_-prefixed ones: batch operations expand them
        # through the metadata naming convention, so passing the full name yields
        # ck_sql_connections_ck_sql_connections_... and finds nothing.
        batch.drop_constraint("password_matches_auth_mode", type_="check")
        batch.drop_constraint("auth_mode_valid", type_="check")
        batch.alter_column(
            "password_encrypted",
            existing_type=sa.Text(),
            nullable=False,
        )
    op.drop_column("sql_connections", "auth_mode")
