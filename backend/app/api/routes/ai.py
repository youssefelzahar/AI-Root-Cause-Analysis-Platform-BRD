"""The AI analyst endpoint.

``POST /api/ai/analyze`` returns **200, not 201**, and creates no resource of its
own - the same reasoning as ``/api/rca/investigations`` and
``/api/anomalies/detections``. It may cause an ``Investigation`` to be created as a
side effect, and it returns that id so a caller can address it, but the answer
itself is not a stored resource. POST rather than GET because it carries a body and
is expensive enough that no intermediary should ever cache it.

``GET /api/ai/health`` is always 200 with an ``ok`` flag, following the SQL Server
connection test: a model daemon being down is a normal, renderable state, and the AI
surface is meant to say so rather than error.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.ai.constants import ANALYST_RULES_VERSION
from app.ai.tools import describe
from app.api.deps import RequestContext, get_current_context, get_db
from app.schemas.ai import (
    AiHealthRead,
    AiToolRead,
    AnalyzeRequest,
    AnalyzeResponse,
    to_response,
)
from app.services import ai_analyst_service

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(
    payload: AnalyzeRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> AnalyzeResponse:
    """Answer a question about a dataset, grounded in one investigation.

    A ``status`` of ``clarification`` is still 200: the question was understood
    well enough to know it cannot be answered as asked, which is an answer. A
    ``status`` of ``partial`` means the analysis succeeded and something optional
    did not - most often the written explanation, in which case every structured
    field is still populated and ``answer_is_template`` says how the prose was
    produced.
    """
    outcome = ai_analyst_service.analyze(
        db,
        payload.dataset_id,
        ctx.company_id,
        question=payload.question,
        user_id=ctx.user_id,
        kpi_definition_id=payload.kpi_definition_id,
        investigation_id=payload.investigation_id,
        refresh=payload.refresh,
    )
    return to_response(outcome, rules_version=ANALYST_RULES_VERSION)


@router.get("/health", response_model=AiHealthRead)
def ai_health() -> AiHealthRead:
    """Whether the configured language model is reachable.

    Unauthenticated in the same sense the rest of this API is, and deliberately
    free of the provider's address: a client needs to know whether asking will
    work, not where the model lives.
    """
    return AiHealthRead(**ai_analyst_service.health())


@router.get("/tools", response_model=list[AiToolRead])
def ai_tools() -> list[AiToolRead]:
    """The analytical operations the analyst is allowed to perform.

    The registry is an allow-list of read and analyse operations, so publishing it
    is safe and makes the boundary inspectable rather than asserted.
    """
    return [AiToolRead(**tool) for tool in describe()]
