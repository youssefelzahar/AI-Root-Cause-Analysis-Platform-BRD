import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPkMixin
from app.db.models.company import Company


class User(UUIDPkMixin, TimestampMixin, Base):
    """Owner of a dataset.

    Phase 1 ships without authentication, so this table deliberately carries no
    credentials. It exists to satisfy the PRD's ``user_id`` metadata
    requirement and to give real auth somewhere to attach later.
    """

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("company_id", "email", name="uq_users_company_email"),)

    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    company: Mapped[Company] = relationship(back_populates="users")
