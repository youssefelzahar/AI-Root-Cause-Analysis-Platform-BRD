"""The evidence layer's own types.

Frozen dataclasses, not Pydantic, for the same reason the RCA package uses them:
this is pure analysis and must stay importable without the web layer. The API
maps these at the boundary in ``app.schemas.investigation``.

``EvidenceRecord`` here is the in-memory record. Its persisted twin is the ORM
class of the same name in ``app.db.models.investigation``; the service maps
between them. Neither is ``app.analysis.rca.models.Evidence``, which is the flat
execution counter object that predates all of this.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.analysis.rca.models import Notice, RcaResult, RcaSpec
from app.analysis.trace import DecisionRecord, QueryRecord
from app.db.models.enums import (
    AuditEventType,
    EvidenceConfidence,
    EvidenceQuality,
    EvidenceType,
    EvidenceValidationStatus,
    InvestigationStatus,
    QualityCheckStatus,
    ReconciliationStatus,
    TreeDriftStatus,
)

# A fixed literal, like the seeded default company id. Evidence ids are derived
# from it, so it is part of the persisted contract and must never change.
EVIDENCE_NAMESPACE = uuid.UUID("6f9b1e2a-0000-5000-8000-000000000001")


def derived_id(investigation_id: uuid.UUID, kind: str, key: str) -> uuid.UUID:
    """A content-derived id, so a record can be referenced before it exists.

    This is what lets the evidence graph stamp evidence ids onto tree nodes with
    no database in sight: with random ids the rows would have to be inserted,
    read back and the tree patched afterwards, dragging the pure builder into the
    service layer.

    uuid5 is SHA-1 based. That is not a weakness here - this is a naming
    function, not a security boundary - and it must not be "upgraded", because
    the digest is part of the persisted contract.
    """
    return uuid.uuid5(EVIDENCE_NAMESPACE, f"{investigation_id}:{kind}:{key}")


def evidence_id(investigation_id: uuid.UUID, evidence_type: EvidenceType, key: str) -> uuid.UUID:
    return derived_id(investigation_id, evidence_type.value, key)


@dataclass(frozen=True)
class Provenance:
    """Where every number in one investigation came from.

    Stamped once and copied onto each record, because a record is meant to be
    checkable on its own - including after it has been fetched by id, with no
    investigation in hand.
    """

    dataset_id: uuid.UUID
    dataset_name: str
    # The storage-key form, never what ``open_dataset_relation`` yields: that
    # embeds an absolute server temp path which differs per request for xlsx, so
    # storing it would leak the server layout and break reproducibility.
    source_relation: str
    measure_column: str
    time_column: str | None
    filters: tuple[dict[str, Any], ...] = ()
    # What DESCRIBE actually returned, so source_columns can be checked against
    # the schema that was really read rather than the one that was expected.
    physical_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceRecord:
    """One structured claim, with everything needed to check it."""

    id: uuid.UUID
    sequence: int
    evidence_type: EvidenceType
    claim: str
    analysis_tool: str

    metric: str | None = None
    dimension: str | None = None
    dimension_value: str | None = None
    dimension_value_is_null: bool = False

    previous_period: str | None = None
    current_period: str | None = None
    previous_value: float | None = None
    current_value: float | None = None
    absolute_change: float | None = None
    percentage_change: float | None = None
    contribution_percentage: float | None = None
    # Kept apart from contribution_percentage on purpose: contribution is how
    # much a segment moved the total, explainability is how far the segments
    # deviated from moving in proportion. It is not a share and may exceed 100%.
    explanatory_power: float | None = None

    filters: tuple[dict[str, Any], ...] = ()
    source_dataset: str = ""
    source_relation: str = ""
    source_columns: tuple[str, ...] = ()
    # The statement that produced these numbers, verbatim, or None for a derived
    # record. Never a reconstruction.
    query: str | None = None
    query_sequence: int | None = None
    # True when this record restates or interprets other evidence rather than
    # reporting a measurement - a drill-down stop reason, for instance. Such a
    # record is *expected* to have no query, so the provenance check must not
    # treat its absence as a gap. Without this, every investigation with a stop
    # reason - which is all of them - would carry a permanent warning.
    derived: bool = False

    validation_status: EvidenceValidationStatus = EvidenceValidationStatus.UNVERIFIED
    confidence: EvidenceConfidence | None = None

    node_id: str | None = None
    depth: int | None = None
    classification: str | None = None
    rank: int = 0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QualityCheck:
    """One of the six evidence-quality checks."""

    check: str
    status: QualityCheckStatus
    detail: str
    # The numbers the check asserted on, so a reader can redo it.
    inputs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Reconciliation:
    """Whether the complete decomposition accounts for the whole movement."""

    status: ReconciliationStatus
    contribution_sum: float | None
    tolerance: float
    basis: str
    tree_drift_status: TreeDriftStatus
    # Drifting nodes and why each one drifts, so DRIFT_EXPLAINED is auditable
    # rather than asserted.
    drifting_nodes: tuple[dict[str, Any], ...] = ()
    detail: str = ""

    @property
    def passed(self) -> bool:
        return (
            self.status is ReconciliationStatus.PASSED
            and self.tree_drift_status is not TreeDriftStatus.DRIFT_UNEXPLAINED
        )


@dataclass(frozen=True)
class EvidenceQualitySummary:
    verdict: EvidenceQuality
    checks: tuple[QualityCheck, ...]
    # Coverage warnings live here rather than degrading the verdict: they
    # describe the data, not the analysis.
    caveats: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuditEvent:
    """One step of an investigation.

    ``elapsed_ms`` is a monotonic offset from the run's start, which is identical
    across two runs of the same data. The service turns it into a wall-clock
    timestamp for display; the offset is what makes the trail reproducible.
    """

    sequence: int
    event_type: AuditEventType
    message: str
    elapsed_ms: int
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InvestigationPlan:
    """What the orchestrator was asked to do.

    The RCA spec plus the identity and provenance the pure layer needs in order
    to build evidence. Assembled by the service, which is the only layer that
    can read the database.
    """

    investigation_id: uuid.UUID
    spec: RcaSpec
    provenance: Provenance
    reconciliation_tolerance: float
    question: str | None = None
    # Anomaly detection is a bounded extra step: it fills the anomaly and trend
    # evidence types, and its failure is a limitation rather than an error.
    run_anomaly_detection: bool = True
    anomaly_grain: str | None = None


@dataclass(frozen=True)
class InvestigationOutcome:
    """Everything one run produced.

    ``status`` is decided here rather than in the service because the reasons for
    PARTIAL are analytical - a skipped step, a truncated dimension, unexplained
    tree drift - and the service has no view of them.
    """

    status: InvestigationStatus
    result: RcaResult
    evidence: tuple[EvidenceRecord, ...]
    decisions: tuple[DecisionRecord, ...]
    audit: tuple[AuditEvent, ...]
    queries: tuple[QueryRecord, ...]
    quality: EvidenceQualitySummary
    reconciliation: Reconciliation
    tree: dict[str, Any] | None
    limitations: tuple[str, ...] = ()
    notices: tuple[Notice, ...] = ()
    anomaly_summary: str | None = None
    started_at: datetime | None = None
