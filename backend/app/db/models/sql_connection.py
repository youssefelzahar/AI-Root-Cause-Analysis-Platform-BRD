import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin, UUIDPkMixin
from app.db.models.enums import SqlAuthMode


class SqlConnection(UUIDPkMixin, TimestampMixin, Base):
    """A saved SQL Server connection (PRD section 8).

    There is no plaintext password column anywhere in the schema - only the
    Fernet token produced by ``app.core.security``.

    Under Windows authentication there is no token either: ``password_encrypted``
    is NULL, because the connection borrows the identity of the process rather
    than presenting a credential. A nullable column is the honest shape for that -
    storing an empty-string ciphertext would make "no password" and "the password
    is blank" indistinguishable.
    """

    __tablename__ = "sql_connections"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_sql_connections_company_name"),
        CheckConstraint(
            "auth_mode IN (" + ", ".join(f"'{mode.value}'" for mode in SqlAuthMode) + ")",
            name="auth_mode_valid",
        ),
        # The invariant the service depends on: SQL auth always has a token,
        # Windows auth never does. Enforced in the database as well as in the
        # schema, because a row that breaks it would fail at connect time with a
        # driver error rather than a validation one.
        CheckConstraint(
            "(auth_mode = 'windows' AND password_encrypted IS NULL) "
            "OR (auth_mode = 'sql' AND password_encrypted IS NOT NULL)",
            name="password_matches_auth_mode",
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=1433, nullable=False)
    database_name: Mapped[str] = mapped_column(String(128), nullable=False)
    auth_mode: Mapped[str] = mapped_column(
        String(20), default=SqlAuthMode.SQL.value, nullable=False
    )
    # Empty under Windows authentication: the login is whoever the backend process
    # is, so there is no user to name. Kept non-nullable so every read site can
    # treat it as a string.
    username: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    password_encrypted: Mapped[str | None] = mapped_column(Text)

    encrypt: Mapped[bool] = mapped_column(default=True, nullable=False)
    trust_server_certificate: Mapped[bool] = mapped_column(default=False, nullable=False)

    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column()
    last_test_error: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
