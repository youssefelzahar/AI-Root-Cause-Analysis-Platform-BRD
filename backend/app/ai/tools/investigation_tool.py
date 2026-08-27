"""``get_investigation`` and ``get_evidence`` - reading a persisted run.

These two are what make a follow-up cheap and a summary possible: the investigation
is already a snapshot, so continuing a conversation about it is a read.

``get_evidence`` selects the types worth quoting in an explanation. It deliberately
omits the six ``validation`` records and the query trace - they are what make the
investigation *checkable*, and they belong in the evidence panel where a reader can
inspect them, not in a prompt where they would crowd out the finding and could not
be quoted anyway.

Each returned claim keeps its ``evidence_id``, which is what lets an answer link
back to the record and its verbatim SQL.
"""

from typing import Any

from app.ai.tools.base import ToolContext, ToolSpec

INVESTIGATION_DESCRIPTION = (
    "The status, verdicts and execution facts of the investigation behind this "
    "answer: evidence quality, reconciliation, rows scanned and what it could not do."
)
EVIDENCE_DESCRIPTION = (
    "The structured claims behind the findings, each with the numbers and the "
    "identifier needed to look up its provenance."
)

# A local model reproduces a handful of claims faithfully and paraphrases twenty.
# The bundle keeps the ones the answer is built from; the full set stays one click
# away in the evidence panel.
MAX_CLAIMS = 12


def run_investigation(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    investigation = context.investigation
    quality = investigation.get("quality_checks") or {}
    result = context.result
    evidence = result.get("evidence") or {}

    return {
        "investigation_id": str(investigation.get("id")),
        "status": investigation.get("status"),
        "analysis_state": investigation.get("analysis_state"),
        "question": investigation.get("question"),
        "evidence_quality": quality.get("verdict") or investigation.get("evidence_quality"),
        "quality_caveats": quality.get("caveats") or [],
        "reconciliation_status": investigation.get("reconciliation_status"),
        "tree_drift_status": investigation.get("tree_drift_status"),
        "rows_scanned": investigation.get("rows_scanned") or evidence.get("total_rows"),
        "rows_in_previous_period": investigation.get("rows_in_previous_period"),
        "rows_in_current_period": investigation.get("rows_in_current_period"),
        "queries_executed": investigation.get("queries_executed"),
        "execution_time_ms": investigation.get("execution_time_ms"),
        "evidence_count": investigation.get("evidence_count"),
        # PARTIAL is not a failure, and these say which optional thing did not
        # happen. An answer that omits them overstates its own completeness.
        "limitations": investigation.get("limitations") or [],
        "notices": [
            {"code": n.get("code"), "severity": n.get("severity"), "message": n.get("message")}
            for n in (investigation.get("notices") or [])
        ],
        "summary": result.get("summary"),
    }


def run_evidence(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    wanted = arguments.get("evidence_types")
    records = context.evidence
    if wanted:
        allowed = set(wanted)
        records = [r for r in records if r.get("evidence_type") in allowed]

    claims = [
        {
            "evidence_id": str(record.get("id")),
            "evidence_type": record.get("evidence_type"),
            "claim": record.get("claim"),
            "dimension": record.get("dimension"),
            "dimension_value": record.get("dimension_value"),
            "absolute_change": record.get("absolute_change"),
            "contribution_percentage": record.get("contribution_percentage"),
            "confidence": record.get("confidence"),
            "validation_status": record.get("validation_status"),
        }
        for record in records[:MAX_CLAIMS]
    ]
    return {
        "claims": claims,
        "returned": len(claims),
        "available": len(context.evidence),
        "omitted": max(0, len(records) - MAX_CLAIMS),
    }


INVESTIGATION_SPEC = ToolSpec(
    name="get_investigation",
    description=INVESTIGATION_DESCRIPTION,
    arguments={},
    run=run_investigation,
)

EVIDENCE_SPEC = ToolSpec(
    name="get_evidence",
    description=EVIDENCE_DESCRIPTION,
    arguments={"evidence_types": False},
    run=run_evidence,
)
