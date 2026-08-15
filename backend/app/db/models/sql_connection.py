import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin, UUIDPkMixin


class SqlConnection(UUIDPkMixin, TimestampMixin, Base):
    """A saved SQL Server connection (PRD section 8).

    There is no plaintext password column anywhere in the schema - only the
    Fernet token produced by ``app.core.security``.
    """

    __tablename__ = "sql_connections"
    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_sql_connections_company_name"),)

    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=1433, nullable=False)
    database_name: Mapped[str] = mapped_column(String(128), nullable=False)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    encrypt: Mapped[bool] = mapped_column(default=True, nullable=False)
    trust_server_certificate: Mapped[bool] = mapped_column(default=False, nullable=False)

    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column()
    last_test_error: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
