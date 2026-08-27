"""Request and response contracts for the AI analyst.

Like ``app.schemas.rca`` and unlike ``app.schemas.investigation``, nothing here is
ORM-backed: the AI layer returns frozen dataclasses and ``to_response`` maps them at
the boundary. That mapping is what keeps Pydantic out of ``app.ai``.

The response is **structured first and prose second**. ``answer`` is a paragraph for
a reader; ``drivers``, ``investigation_id`` and ``evidence_ids`` are what the UI
renders and what a caller should trust. Recovering a number by parsing the prose
would reintroduce exactly the fragility the evidence layer removed - so every figure
worth acting on has its own field.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.ai.models import AnalystOutcome, EvidenceBundle, GroundedDriver


class AnalyzeRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    dataset_id: UUID
    # Defaults to the dataset's active definition, and is overridden by whatever
    # the question names when that is unambiguous.
    kpi_definition_id: UUID | None = None
    # Continues an existing investigation instead of running a new one, which is
    # what makes a follow-up a read rather than a recompute.
    investigation_id: UUID | None = None
    # Forces a fresh analysis even when an equivalent completed one exists.
    refresh: bool = False


class ClarificationRead(BaseModel):
    """A question that could not be answered as asked.

    Carries the options, because "I found Revenue and Sales Volume" is answerable
    and "the KPI is ambiguous" is not.
    """

    code: str
    message: str
    options: list[str] = Field(default_factory=list)


class AnalystDriverRead(BaseModel):
    dimension: str
    value: str
    absolute_change: float | None = None
    # Percentage points of the movement, already multiplied, so no consumer has to
    # know whether it received a share or a percentage.
    contribution_percentage: float | None = None
    classification: str
    rank: int = 0
    is_new_segment: bool = False
    is_lost_segment: bool = False
    # The record behind this driver, for a link into the evidence panel.
    evidence_id: UUID | None = None


class AnalystStepRead(BaseModel):
    """One analysis step, for the progress list the UI shows."""

    tool: str
    ok: bool
    duration_ms: int
    detail: str = ""


class AnalystEvidenceRead(BaseModel):
    """The grounded numbers, as the answer was allowed to see them.

    Returned in full rather than summarised: it is the audit surface for the prose.
    A reader who doubts a sentence can compare it against this without a round trip.
    """

    kpi_name: str
    previous_period: str | None = None
    current_period: str | None = None
    previous_value: float | None = None
    current_value: float | None = None
    absolute_change: float | None = None
    percentage_change: float | None = None
    direction: str | None = None
    severity: str | None = None
    attribution_basis: str | None = None
    change_pattern: str | None = None
    drill_path: list[str] = Field(default_factory=list)
    drill_stop_reason: str | None = None
    anomaly_summary: str | None = None
    evidence_quality: str | None = None
    reconciliation_status: str | None = None
    facts: list[dict[str, Any]] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    """One answered question.

    ``answer`` may be null while ``status`` is ``partial`` and every structured
    field is populated. That is the case where the analysis succeeded and the model
    did not, and it is deliberate: the investigation is the valuable part.
    """

    status: str
    question: str
    intent: str | None = None
    answer: str | None = None
    # True when the prose was assembled from the evidence rather than generated -
    # because the model was unavailable, or because what it wrote quoted a figure
    # the analysis never produced. A reader is never shown one as the other.
    answer_is_template: bool = False
    clarification: ClarificationRead | None = None
    investigation_id: UUID | None = None
    drivers: list[AnalystDriverRead] = Field(default_factory=list)
    offsetting_factors: list[AnalystDriverRead] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    evidence: AnalystEvidenceRead | None = None
    steps: list[AnalystStepRead] = Field(default_factory=list)
    # What was assumed on the reader's behalf - a substituted KPI, a period that
    # could not be targeted. Stated rather than applied silently.
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    model: str | None = None
    rules_version: str
    duration_ms: int = 0


class AiHealthRead(BaseModel):
    """Whether the model is reachable.

    Always 200: a provider being down is a normal, renderable state, and the AI
    surface is meant to say so while still offering the analysis. Same reasoning as
    the SQL Server connection test.
    """

    ok: bool
    enabled: bool
    provider: str
    model: str | None = None
    message: str = ""
    error_code: str | None = None
    latency_ms: int = 0


class AiToolRead(BaseModel):
    """One tool the analyst can use, for a client that wants to show them."""

    name: str
    description: str
    arguments: list[dict[str, Any]] = Field(default_factory=list)


def _driver(driver: GroundedDriver) -> AnalystDriverRead:
    return AnalystDriverRead(
        dimension=driver.dimension,
        value=driver.value,
        absolute_change=driver.absolute_change,
        contribution_percentage=driver.contribution_percentage,
        classification=driver.classification,
        rank=driver.rank,
        is_new_segment=driver.is_new_segment,
        is_lost_segment=driver.is_lost_segment,
        evidence_id=UUID(driver.evidence_id) if driver.evidence_id else None,
    )


def _evidence(bundle: EvidenceBundle) -> AnalystEvidenceRead:
    return AnalystEvidenceRead(
        kpi_name=bundle.kpi_name,
        previous_period=bundle.previous_period,
        current_period=bundle.current_period,
        previous_value=bundle.previous_value,
        current_value=bundle.current_value,
        absolute_change=bundle.absolute_change,
        percentage_change=bundle.percentage_change,
        direction=bundle.direction,
        severity=bundle.severity,
        attribution_basis=bundle.attribution_basis,
        change_pattern=bundle.change_pattern,
        drill_path=list(bundle.drill_path),
        drill_stop_reason=bundle.drill_stop_reason,
        anomaly_summary=bundle.anomaly_summary,
        evidence_quality=bundle.evidence_quality,
        reconciliation_status=bundle.reconciliation_status,
        facts=[
            {"label": fact.label, "value": fact.value, "formatted": fact.formatted}
            for fact in bundle.facts
        ],
    )


def to_response(outcome: AnalystOutcome, *, rules_version: str) -> AnalyzeResponse:
    """Map the AI layer's dataclasses onto the wire contract."""
    bundle = outcome.bundle
    return AnalyzeResponse(
        status=outcome.status.value,
        question=outcome.question,
        intent=outcome.intent.value if outcome.intent else None,
        answer=outcome.answer,
        answer_is_template=outcome.answer_is_template,
        clarification=(
            ClarificationRead(
                code=outcome.clarification.code,
                message=outcome.clarification.message,
                options=list(outcome.clarification.options),
            )
            if outcome.clarification
            else None
        ),
        investigation_id=outcome.investigation_id,
        drivers=[_driver(d) for d in (bundle.drivers if bundle else ())],
        offsetting_factors=[_driver(d) for d in (bundle.offsetting if bundle else ())],
        evidence_ids=[UUID(value) for value in (bundle.evidence_ids() if bundle else ())],
        evidence=_evidence(bundle) if bundle else None,
        steps=[
            AnalystStepRead(
                tool=step.tool, ok=step.ok, duration_ms=step.duration_ms, detail=step.detail
            )
            for step in outcome.steps
        ],
        assumptions=list(outcome.assumptions),
        limitations=list(outcome.limitations),
        model=outcome.model,
        rules_version=rules_version,
        duration_ms=outcome.duration_ms,
    )
