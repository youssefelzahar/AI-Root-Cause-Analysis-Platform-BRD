"""Shared FastAPI dependencies."""

import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.db.models import Company, User
from app.db.session import get_db

__all__ = ["RequestContext", "get_current_context", "get_db", "pagination"]


@dataclass(frozen=True)
class RequestContext:
    company_id: uuid.UUID
    user_id: uuid.UUID | None


def get_current_context(
    db: Session = Depends(get_db),
    x_company_id: uuid.UUID | None = Header(default=None, alias="X-Company-Id"),
    x_user_id: uuid.UUID | None = Header(default=None, alias="X-User-Id"),
) -> RequestContext:
    """Resolve the acting company and user.

    Phase 1 ships without authentication, so this returns the seeded default
    context. It is the single seam where real auth attaches later: no route
    signature, repository or service needs to change.

    The header overrides exist so multi-tenant isolation is testable.
    """
    company_id = x_company_id or settings.default_company_id
    user_id = x_user_id or settings.default_user_id

    company = db.scalar(select(Company).where(Company.id == company_id))
    if company is None:
        raise AppError(
            "The configured company does not exist. Run database migrations.",
            code="CONTEXT_NOT_FOUND",
            status_code=400,
        )

    user = db.scalar(select(User).where(User.id == user_id, User.company_id == company_id))
    return RequestContext(company_id=company_id, user_id=user.id if user else None)


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int


def pagination(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Pagination:
    return Pagination(limit=limit, offset=offset)
