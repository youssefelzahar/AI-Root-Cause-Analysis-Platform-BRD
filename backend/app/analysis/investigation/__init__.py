"""The evidence and investigation layer.

Orchestrates the existing analysis engines and turns their findings into
structured, checkable evidence: a claim per finding, provenance to verify it, a
reconciliation verdict, a quality summary, a decision trace and an audit trail.

Pure analysis, like ``app.analysis.rca`` and ``app.analysis.anomaly``: nothing
here imports SQLAlchemy, Pydantic or storage, so the whole layer is testable
without a database or an HTTP client. ``app.services.investigation_service`` is
what persists an outcome.

A note on names. ``EvidenceRecord`` in this package is one structured claim.
``app.analysis.rca.models.Evidence`` and ``app.analysis.anomaly.models.Evidence``
are the flat execution-counter objects that predate it and are unrelated; both
are deliberately left alone.
"""

from app.analysis.investigation.engine import investigate
from app.analysis.investigation.models import (
    AuditEvent,
    EvidenceQualitySummary,
    EvidenceRecord,
    InvestigationOutcome,
    InvestigationPlan,
    Provenance,
    QualityCheck,
    Reconciliation,
    evidence_id,
)

__all__ = [
    "AuditEvent",
    "EvidenceQualitySummary",
    "EvidenceRecord",
    "InvestigationOutcome",
    "InvestigationPlan",
    "Provenance",
    "QualityCheck",
    "Reconciliation",
    "evidence_id",
    "investigate",
]
