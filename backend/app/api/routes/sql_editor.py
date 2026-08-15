"""SQL editor: validate, execute, and save output as a dataset (PRD section 8)."""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import RequestContext, get_current_context, get_db
from app.connectors.sql_guard import check_sql
from app.schemas.dataset import DatasetDetail
from app.schemas.sql import (
    SaveQueryAsDatasetRequest,
    SqlExecuteRequest,
    SqlExecuteResult,
    SqlGuardRead,
    SqlValidateRequest,
)
from app.services import sql_service
from app.services.jobs import enqueue_profiling

router = APIRouter(prefix="/sql", tags=["sql"])


@router.post("/validate", response_model=SqlGuardRead)
def validate_sql(payload: SqlValidateRequest) -> SqlGuardRead:
    """Lint a statement without running it. Always 200 - this is not an action."""
    result = check_sql(payload.sql)
    return SqlGuardRead(
        allowed=result.allowed,
        statement_type=result.statement_type,
        reasons=result.reasons,
        normalized_sql=result.normalized_sql,
    )


@router.post("/connections/{connection_id}/execute", response_model=SqlExecuteResult)
def execute_query(
    connection_id: uuid.UUID,
    payload: SqlExecuteRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> SqlExecuteResult:
    """Run a read-only query.

    The guard runs before any connection is opened, so a rejected statement
    never reaches SQL Server.
    """
    connection = sql_service.get_connection(db, connection_id, ctx.company_id)
    result = sql_service.execute(
        db,
        connection,
        payload.sql,
        row_limit=payload.row_limit or 0,
        timeout_seconds=payload.timeout_seconds or 0,
    )
    return SqlExecuteResult(
        columns=result.columns,
        rows=[[None if v is None else str(v) for v in row] for row in result.rows],
        row_count=result.row_count,
        truncated=result.truncated,
        elapsed_ms=result.elapsed_ms,
    )


@router.post(
    "/connections/{connection_id}/save-as-dataset",
    response_model=DatasetDetail,
    status_code=status.HTTP_201_CREATED,
)
def save_query_as_dataset(
    connection_id: uuid.UUID,
    payload: SaveQueryAsDatasetRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> DatasetDetail:
    """Materialise a query result as an internal dataset.

    The result then flows through the same validation and profiling pipeline as
    an uploaded file.
    """
    from app.api.routes.datasets import to_detail

    connection = sql_service.get_connection(db, connection_id, ctx.company_id)
    dataset = sql_service.save_query_as_dataset(
        db,
        connection,
        sql=payload.sql,
        dataset_name=payload.dataset_name,
        description=payload.description,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        max_rows=payload.max_rows,
    )
    enqueue_profiling(dataset.id, background_tasks)
    db.refresh(dataset)
    return to_detail(db, dataset)
