"""The AI analyst layer: understand the question, explain the evidence.

Turns a natural-language question into one of the investigations the platform
already knows how to run, then explains what that investigation found. It adds no
analysis of its own - every number in an answer was computed by
``app.analysis.rca`` or ``app.analysis.anomaly`` and persisted by
``app.services.investigation_service`` before the LLM ever saw it.

The layer is deliberately shaped so the model appears exactly twice:

1. ``intent.understand`` - question to a typed ``Intent``.
2. ``explain.explain`` - a grounded ``EvidenceBundle`` to prose.

Everything between the two is ordinary Python: ``resolve`` maps the intent onto
real KPI definitions, ``planner`` picks a fixed recipe, ``executor`` runs it
against the tool registry, ``grounding`` collects the numbers from persisted rows,
and ``verify`` checks the answer invented none of them. That is what makes the
whole path testable without a model running, and what keeps a wrong guess from the
model into a wrong number on the page.

Pure in the same sense as ``app.analysis``: nothing here imports SQLAlchemy or
storage. ``app.services.ai_analyst_service`` is the only layer that can read the
database, and it is what hands this one an already-persisted investigation.
"""

from app.ai.models import (
    AnalystOutcome,
    AnalystStatus,
    EvidenceBundle,
    Intent,
    IntentKind,
    Plan,
    ResolvedContext,
    ToolCall,
    ToolResult,
)

__all__ = [
    "AnalystOutcome",
    "AnalystStatus",
    "EvidenceBundle",
    "Intent",
    "IntentKind",
    "Plan",
    "ResolvedContext",
    "ToolCall",
    "ToolResult",
]
