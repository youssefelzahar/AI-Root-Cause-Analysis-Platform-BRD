from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorBody(BaseModel):
    code: str
    message: str
    details: object | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class ContextRead(BaseModel):
    company_id: str
    company_name: str
    user_id: str
    user_name: str
    user_email: str
    # Phase 1 ships without authentication; the frontend uses this to avoid
    # rendering any account UI.
    authenticated: bool = Field(default=False)
