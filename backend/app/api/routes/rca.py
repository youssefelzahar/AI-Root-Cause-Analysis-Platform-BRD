"""Root cause analysis endpoints (PRD section 14)."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import RequestContext, get_current_context, get_db
from app.schemas.rca import InvestigationRequest, InvestigationResult, to_result
from app.services import dataset_service, kpi_service, rca_service

router = APIRouter(prefix="/rca", tags=["rca"])


@router.post("/investigations", response_model=InvestigationResult)
def create_investigation(
    payload: InvestigationRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> InvestigationResult:
    """Explain a KPI's period-over-period change.

    200 rather than 201: the analysis is stateless and creates no resource. POST
    rather than GET because it carries a body and is expensive enough that it
    must never be cached by an intermediary.
    """
    dataset, definition, result = rca_service.run(
        db,
        payload.dataset_id,
        ctx.company_id,
        kpi_definition_id=payload.kpi_definition_id,
        max_drivers=payload.max_drivers,
        max_tree_depth=payload.max_tree_depth,
    )
    return to_result(
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        kpi_definition_id=definition.id,
        generated_at=datetime.now(UTC),
        result=result,
    )


@router.delete("/investigations/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_investigation(
    dataset_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> Response:
   
    dataset = dataset_service.get_dataset(db, dataset_id, ctx.company_id)
    definition = kpi_service.get_active_definition(db, dataset)
    kpi_service.delete_definition(db, dataset, definition.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
