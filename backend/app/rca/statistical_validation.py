"""Statistical validation of candidate drivers.

Phase 1 provides the seam and a simple magnitude-based confidence. The full
significance testing described in PRD section 14 belongs to the RCA phase.
"""

from app.schemas.rca import DriverFinding


def confidence_for(finding: DriverFinding) -> float:
    """A crude 0-1 confidence from the share of total movement explained."""
    return round(min(1.0, finding.contribution_pct / 100), 4)


def annotate(findings: list[DriverFinding]) -> list[dict]:
    return [
        {
            "dimension": finding.dimension,
            "value": finding.value,
            "contribution_pct": finding.contribution_pct,
            "confidence": confidence_for(finding),
        }
        for finding in findings
    ]
