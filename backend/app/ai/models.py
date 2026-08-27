"""The AI layer's own types.

Frozen dataclasses, not Pydantic, for the reason ``app.analysis.rca.models`` gives:
this is a pure layer and must stay importable without the web stack. The API maps
these at the boundary in ``app.schemas.ai``.

One rule runs through the whole module. **No field here may describe something the
executor cannot honour.** The engines take four analytical parameters - the
dataset, the KPI definition, ``max_drivers`` and ``max_tree_depth`` - and derive
everything else from the persisted KPI row. An ``Intent`` carrying a
``measure_column`` or an explicit date range would be a promise the layer below
cannot keep, and the model would learn to fill it in.
"""

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class IntentKind(str, Enum):
    """What the question is asking for.

    A ``str`` enum for the same reason the domain enums are: the value is what
    crosses the wire and what appears in a prompt's few-shot examples.
    """

    KPI_ANALYSIS = "KPI_ANALYSIS"
    ROOT_CAUSE_ANALYSIS = "ROOT_CAUSE_ANALYSIS"
    ANOMALY_ANALYSIS = "ANOMALY_ANALYSIS"
    DIMENSION_ANALYSIS = "DIMENSION_ANALYSIS"
    CONTRIBUTION_ANALYSIS = "CONTRIBUTION_ANALYSIS"
    DRILL_DOWN = "DRILL_DOWN"
    INVESTIGATION_SUMMARY = "INVESTIGATION_SUMMARY"
    FOLLOW_UP_ANALYSIS = "FOLLOW_UP_ANALYSIS"


class AnalystStatus(str, Enum):
    """How one question ended.

    ``CLARIFICATION`` is not a failure and not an error: the question was
    understood well enough to know it cannot be answered as asked. ``PARTIAL``
    means the analysis succeeded and something optional did not - most often the
    explanation, which is exactly the case where the structured answer still has
    everything a reader needs.
    """

    COMPLETED = "completed"
    CLARIFICATION = "clarification"
    PARTIAL = "partial"


class HintSource(str, Enum):
    """Where a resolved value came from.

    Recorded so an answer can say *"you asked about sales, I analysed Revenue"*
    rather than quietly substituting. A substitution nobody is told about is
    indistinguishable from a wrong answer.
    """

    QUESTION = "question"
    ACTIVE_DEFAULT = "active_default"
    CARRIED_OVER = "carried_over"


@dataclass(frozen=True)
class Intent:
    """What the model understood, after sanitisation.

    Every hint is optional and every hint is a *claim about the question*, never
    an instruction to the engines. ``resolve`` decides what, if anything, each one
    maps onto.
    """

    kind: IntentKind
    kpi_hint: str | None = None
    period_hint: str | None = None
    dimension_hint: str | None = None
    segment_hint: str | None = None
    # Set when a hint was dropped during sanitisation, so the reason is visible in
    # the response rather than only in a log line.
    dropped_hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class KpiChoice:
    """One KPI definition the question might have meant."""

    kpi_definition_id: uuid.UUID
    name: str
    measure_column: str
    aggregation: str
    is_active: bool


@dataclass(frozen=True)
class Clarification:
    """A question that cannot be answered as asked.

    Carries the options rather than only the complaint: *"I found Revenue and
    Sales Volume"* is answerable, *"the KPI is ambiguous"* is not.
    """

    code: str
    message: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedContext:
    """What the intent turned out to mean against this dataset.

    Assembled without an LLM. ``clarification`` being set is what stops the
    pipeline before any analysis runs.
    """

    dataset_id: uuid.UUID
    dataset_name: str
    kpi_definition_id: uuid.UUID | None = None
    kpi_name: str | None = None
    kpi_source: HintSource = HintSource.ACTIVE_DEFAULT
    # Validated against the KPI definition's own dimension list, so a hallucinated
    # dimension never reaches a tool argument.
    dimension: str | None = None
    segment: str | None = None
    investigation_id: uuid.UUID | None = None
    # The period the question named, kept verbatim and unparsed. The engines
    # anchor on the data's own latest timestamp, so this is something to reconcile
    # against the resolved windows and report on - never something to compute
    # from.
    period_claim: str | None = None
    clarification: Clarification | None = None
    assumptions: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        return self.clarification is not None


@dataclass(frozen=True)
class ToolCall:
    """One step of a plan.

    ``arguments`` is built by the planner from the resolved context, never by the
    model. The registry validates it anyway - a planner bug and a model
    hallucination should fail the same way.
    """

    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Plan:
    """The fixed sequence this intent will execute."""

    intent: IntentKind
    calls: tuple[ToolCall, ...]

    def __len__(self) -> int:
        return len(self.calls)


@dataclass(frozen=True)
class ToolResult:
    """What one step produced.

    ``ok`` False is a reportable outcome rather than an exception: a step that
    found nothing to say - a drill-down into a segment the tree never expanded -
    is a finding, and the explanation should get to see it.
    """

    tool: str
    ok: bool
    duration_ms: int
    payload: dict[str, Any] = field(default_factory=dict)
    detail: str = ""


@dataclass(frozen=True)
class GroundedFact:
    """One number the answer is allowed to use, and where it came from.

    The ``evidence_id`` is what makes the bundle checkable: a reader can fetch
    that record and see the claim, the provenance and the statement behind it.
    """

    label: str
    value: float | None
    formatted: str
    evidence_id: str | None = None


@dataclass(frozen=True)
class GroundedDriver:
    """One segment, as the answer may describe it."""

    dimension: str
    value: str
    absolute_change: float | None
    contribution_percentage: float | None
    classification: str
    rank: int
    is_new_segment: bool = False
    is_lost_segment: bool = False
    evidence_id: str | None = None


@dataclass(frozen=True)
class EvidenceBundle:
    """Everything the explanation is allowed to know.

    The LLM sees this and nothing else - not the dataset, not the SQL, not the
    request. Anything absent here cannot appear in an answer, which is what turns
    "do not invent numbers" into a property of the input rather than a plea in the
    prompt.
    """

    question: str
    kpi_name: str
    investigation_id: uuid.UUID
    investigation_status: str
    analysis_state: str
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
    drivers: tuple[GroundedDriver, ...] = ()
    offsetting: tuple[GroundedDriver, ...] = ()
    # Whether the contribution step actually ran. Without this, an empty
    # ``drivers`` is ambiguous between "nothing was material enough to name" - a
    # real finding - and "nobody looked", and the explanation would state the first
    # while meaning the second. An answer must never report an absence it did not
    # check for.
    contribution_analysed: bool = False
    drill_path: tuple[str, ...] = ()
    drill_stop_reason: str | None = None
    anomaly_summary: str | None = None
    facts: tuple[GroundedFact, ...] = ()
    evidence_quality: str | None = None
    reconciliation_status: str | None = None
    limitations: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()

    def numbers(self) -> tuple[float, ...]:
        """Every value an answer may legitimately state.

        Used by ``verify`` to decide whether a figure in the generated prose was
        measured or invented.
        """
        values: list[float] = []
        for candidate in (
            self.previous_value,
            self.current_value,
            self.absolute_change,
            self.percentage_change,
        ):
            if candidate is not None:
                values.append(candidate)
        for group in (self.drivers, self.offsetting):
            for driver in group:
                if driver.absolute_change is not None:
                    values.append(driver.absolute_change)
                if driver.contribution_percentage is not None:
                    values.append(driver.contribution_percentage)
        for fact in self.facts:
            if fact.value is not None:
                values.append(fact.value)
        return tuple(values)

    def evidence_ids(self) -> tuple[str, ...]:
        seen: list[str] = []
        for fact in self.facts:
            if fact.evidence_id and fact.evidence_id not in seen:
                seen.append(fact.evidence_id)
        for group in (self.drivers, self.offsetting):
            for driver in group:
                if driver.evidence_id and driver.evidence_id not in seen:
                    seen.append(driver.evidence_id)
        return tuple(seen)


@dataclass(frozen=True)
class AnalystOutcome:
    """Everything one question produced.

    ``answer`` may be None while ``status`` is PARTIAL and every structured field
    is populated. That is the case where the analysis worked and the model did
    not, and it is a deliberate shape: the investigation is the valuable part and
    it must survive an unavailable LLM.
    """

    status: AnalystStatus
    question: str
    intent: IntentKind | None = None
    answer: str | None = None
    # True when the answer text was built from the bundle by template rather than
    # generated, because the model was unavailable or its prose failed the numeric
    # check. Surfaced so a reader is never told a template was a model's work.
    answer_is_template: bool = False
    clarification: Clarification | None = None
    investigation_id: uuid.UUID | None = None
    bundle: EvidenceBundle | None = None
    steps: tuple[ToolResult, ...] = ()
    assumptions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    model: str | None = None
    duration_ms: int = 0
