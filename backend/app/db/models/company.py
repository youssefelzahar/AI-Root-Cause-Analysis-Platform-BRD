from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPkMixin

if TYPE_CHECKING:
    from app.db.models.user import User


class Company(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str | None] = mapped_column(String(100), unique=True)

    users: Mapped[list["User"]] = relationship(back_populates="company", cascade="all, delete-orphan")
