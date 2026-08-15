"""RCA investigation endpoints.

These predate Phase 1 and are preserved unchanged so the existing dashboard
keeps working. The full RCA engine is PRD section 14 (future phase).
"""

from fastapi import APIRouter

from app.schemas.rca import InvestigationRequest, InvestigationResult
from app.services.rca_engine import analyze

router = APIRouter(tags=["rca"])


@router.post("/investigations", response_model=InvestigationResult)
def create_investigation(payload: InvestigationRequest) -> InvestigationResult:
    return analyze(payload)


@router.get("/demo", response_model=InvestigationRequest)
def demo_payload() -> InvestigationRequest:
    return InvestigationRequest(
        metric_name="Revenue",
        baseline_period=[
            {"date": "2026-08-01", "value": 12000, "dimensions": {"region": "North", "channel": "Paid"}},
            {"date": "2026-08-01", "value": 9000, "dimensions": {"region": "South", "channel": "Organic"}},
            {"date": "2026-08-02", "value": 12200, "dimensions": {"region": "North", "channel": "Paid"}},
            {"date": "2026-08-02", "value": 9400, "dimensions": {"region": "South", "channel": "Organic"}},
        ],
        comparison_period=[
            {"date": "2026-08-08", "value": 8700, "dimensions": {"region": "North", "channel": "Paid"}},
            {"date": "2026-08-08", "value": 9300, "dimensions": {"region": "South", "channel": "Organic"}},
            {"date": "2026-08-09", "value": 8500, "dimensions": {"region": "North", "channel": "Paid"}},
            {"date": "2026-08-09", "value": 9100, "dimensions": {"region": "South", "channel": "Organic"}},
        ],
        dimensions=["region", "channel"],
    )
