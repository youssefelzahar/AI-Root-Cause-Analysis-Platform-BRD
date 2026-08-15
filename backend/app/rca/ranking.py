"""Root-cause ranking."""

from app.schemas.rca import DriverFinding

TOP_N = 8


def rank(findings: list[DriverFinding], limit: int = TOP_N) -> list[DriverFinding]:
    return sorted(findings, key=lambda item: item.contribution_pct, reverse=True)[:limit]
