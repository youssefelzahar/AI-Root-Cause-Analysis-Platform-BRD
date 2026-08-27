"""Tool contracts.

Each tool is a named, typed, validated operation over an investigation the service
layer has already run and persisted. The registry is an **allow-list**: a name that
is not in it is a validation failure, never a dispatch. That matters more here than
it might elsewhere, because one route in this API is misnamed - ``DELETE
/api/rca/investigations/{dataset_id}`` deletes the dataset's active KPI definition -
and an allow-list is what guarantees no amount of model creativity reaches it.

A tool receives a ``ToolContext`` and returns a plain dict. It never opens a
database session, never runs SQL, and never computes a number: it selects from what
the investigation already produced. That is why five of the six analytical tools are
projections - see the module docstring in ``app.ai.tools``.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.exceptions import ValidationError


@dataclass
class ToolContext:
    """Everything a tool may read.

    Deliberately not a database session. The service layer resolves and persists
    the investigation first, then hands the *result* here - so a tool cannot widen
    its own access, and the whole registry is testable from a dict.

    ``investigation`` is the persisted row's payload as plain data:
    ``{"id", "status", "analysis_state", "result", "tree", "quality_checks", ...}``.
    """

    investigation: dict[str, Any]
    evidence: list[dict[str, Any]] = field(default_factory=list)
    anomaly: dict[str, Any] | None = None
    dimension: str | None = None
    segment: str | None = None

    @property
    def result(self) -> dict[str, Any]:
        """The RCA findings payload, or an empty dict.

        Empty rather than None so every tool can index it without a guard: an
        investigation that reached a terminal state with no decomposition is a
        normal outcome, not a missing field.
        """
        return self.investigation.get("result") or {}


@dataclass(frozen=True)
class ToolSpec:
    """One tool's full contract.

    ``description`` is written for a reader, not for a model - the model never
    chooses a tool in this design. It is what the API exposes so a client can
    render "what the analyst can do" without reading Python.
    """

    name: str
    description: str
    # Argument name to whether it is required. A tiny schema on purpose: arguments
    # come from the planner, so this exists to catch a planner bug, not to parse
    # arbitrary input.
    arguments: dict[str, bool]
    run: Callable[[ToolContext, dict[str, Any]], dict[str, Any]]

    def validate(self, arguments: dict[str, Any]) -> None:
        """Reject an argument set this tool cannot honour."""
        unknown = set(arguments) - set(self.arguments)
        if unknown:
            raise ValidationError(
                f"{self.name} does not accept {', '.join(sorted(unknown))}.",
                code="AI_INVALID_TOOL_ARGUMENTS",
                details={"tool": self.name, "unknown": sorted(unknown)},
            )
        for argument, required in self.arguments.items():
            if required and arguments.get(argument) in (None, ""):
                raise ValidationError(
                    f"{self.name} requires {argument}.",
                    code="AI_INVALID_TOOL_ARGUMENTS",
                    details={"tool": self.name, "missing": argument},
                )
