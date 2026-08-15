"""Saved SQL Server connections (PRD section 8)."""

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, RequestContext, get_current_context, get_db, pagination
from app.db.models import SqlConnection
from app.schemas.common import Page
from app.schemas.sql import (
    SqlConnectionCreate,
    SqlConnectionRead,
    SqlConnectionTestResult,
    SqlConnectionUpdate,
)
from app.services import sql_service

router = APIRouter(prefix="/sql-connections", tags=["sql"])


@router.post("", response_model=SqlConnectionRead, status_code=status.HTTP_201_CREATED)
def create_connection(
    payload: SqlConnectionCreate,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> SqlConnectionRead:
    connection = sql_service.create_connection(
        db, payload.to_payload(), company_id=ctx.company_id, user_id=ctx.user_id
    )
    return SqlConnectionRead.model_validate(connection)


@router.get("", response_model=Page[SqlConnectionRead])
def list_connections(
    page: Pagination = Depends(pagination),
    search: str | None = Query(default=None),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> Page[SqlConnectionRead]:
    conditions = [SqlConnection.company_id == ctx.company_id]
    if search:
        conditions.append(SqlConnection.name.ilike(f"%{search}%"))
    total = db.scalar(select(func.count()).select_from(SqlConnection).where(*conditions)) or 0
    rows = db.scalars(
        select(SqlConnection)
        .where(*conditions)
        .order_by(SqlConnection.created_at.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return Page[SqlConnectionRead](
        items=[SqlConnectionRead.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/test", response_model=SqlConnectionTestResult)
def test_unsaved_connection(payload: SqlConnectionCreate) -> SqlConnectionTestResult:
    """Test credentials before saving them. Nothing is persisted."""
    return SqlConnectionTestResult(**sql_service.test_unsaved(payload.to_payload()))


@router.get("/{connection_id}", response_model=SqlConnectionRead)
def get_connection(
    connection_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> SqlConnectionRead:
    return SqlConnectionRead.model_validate(
        sql_service.get_connection(db, connection_id, ctx.company_id)
    )


@router.patch("/{connection_id}", response_model=SqlConnectionRead)
def update_connection(
    connection_id: uuid.UUID,
    payload: SqlConnectionUpdate,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> SqlConnectionRead:
    connection = sql_service.get_connection(db, connection_id, ctx.company_id)
    return SqlConnectionRead.model_validate(
        sql_service.update_connection(db, connection, payload.to_payload())
    )


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection(
    connection_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> Response:
    connection = sql_service.get_connection(db, connection_id, ctx.company_id)
    db.delete(connection)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{connection_id}/test", response_model=SqlConnectionTestResult)
def test_connection(
    connection_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> SqlConnectionTestResult:
    """Probe a saved connection.

    Returns 200 with ``ok: false`` when the server is unreachable - an
    unreachable database is a normal outcome to render, not an API error.
    """
    connection = sql_service.get_connection(db, connection_id, ctx.company_id)
    return SqlConnectionTestResult(**sql_service.test_connection(db, connection))


@router.get("/{connection_id}/schema")
def browse_schema(
    connection_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> dict:
    from app.connectors import sqlserver

    connection = sql_service.get_connection(db, connection_id, ctx.company_id)
    return sqlserver.list_schema(sql_service._params(connection))
