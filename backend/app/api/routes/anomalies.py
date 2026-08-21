"""Anomaly detection endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import RequestContext, get_current_context, get_db
from app.schemas.anomaly import DetectionRequest, DetectionResult, to_report
from app.services import anomaly_service

router = APIRouter(prefix="/anomalies", tags=["anomalies"])


@router.post("/detections", response_model=DetectionResult)
def create_detection(
    payload: DetectionRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> DetectionResult:
    """Judge whether a KPI is behaving unusually against its own history.

    200 rather than 201: the detection is stateless and creates no resource.
    POST rather than GET because it carries a body and is expensive enough that
    it must never be cached by an intermediary.
    """
    dataset, definition, report = anomaly_service.run(
        db,
        payload.dataset_id,
        ctx.company_id,
        kpi_definition_id=payload.kpi_definition_id,
        grain=payload.grain,
        method=payload.method,
        baseline_window=payload.baseline_window,
    )
    return to_report(
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        kpi_definition_id=definition.id,
        generated_at=datetime.now(UTC),
        measure_column=definition.column_name,
        time_column=definition.time_column,
        baseline_window=payload.baseline_window,
        report=report,
    )
