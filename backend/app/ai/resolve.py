"""Step 2: what the intent means against this dataset. No model involved.

The boundary between "what the question said" and "what the system will do".
Everything here is deterministic string work, because a KPI is a persisted row with
a name and matching it is a lookup, not a judgement. An embedding here would make
the choice unexplainable and untestable for no accuracy anyone could point to.

The ladder is the design. A dataset has exactly one *active* KPI definition - a
partial unique index enforces it - so ambiguity is rarer than the specification
implies, and the interesting cases are "the hint matched nothing" and "the hint
matched a superseded definition too". Both are answered by naming what was found
rather than by guessing.
"""

import re
import uuid

from app.ai.constants import PERIOD_SUBSTITUTION_NOTE
from app.ai.models import (
    Clarification,
    HintSource,
    Intent,
    IntentKind,
    KpiChoice,
    ResolvedContext,
)

_WORD = re.compile(r"[a-z0-9]+")


def _normalise(text: str) -> str:
    """Case-folded, punctuation-free, for comparing a hint to a name."""
    return "".join(_WORD.findall(text.lower()))


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def match_kpis(hint: str, choices: tuple[KpiChoice, ...]) -> tuple[KpiChoice, ...]:
    """Every definition the hint could plausibly mean, best tier only.

    Three tiers, tried in order and never mixed: an exact match beats a substring
    match, which beats a shared-word match. Mixing them is what turns "Revenue"
    into an ambiguity against "Revenue Growth Rate" - the exact hit is obviously
    the answer, and returning both would ask the user a question with a known
    answer.
    """
    needle = _normalise(hint)
    if not needle:
        return ()

    exact = [c for c in choices if _normalise(c.name) == needle or _normalise(c.measure_column) == needle]
    if exact:
        return tuple(exact)

    partial = [
        c
        for c in choices
        if needle in _normalise(c.name)
        or _normalise(c.name) in needle
        or needle in _normalise(c.measure_column)
    ]
    if partial:
        return tuple(partial)

    hint_tokens = _tokens(hint)
    overlap = [
        c
        for c in choices
        if hint_tokens & (_tokens(c.name) | _tokens(c.measure_column))
    ]
    return tuple(overlap)


def match_dimension(hint: str | None, dimensions: tuple[str, ...]) -> str | None:
    """A dimension name, or None. Never the hint itself.

    Returning the validated column rather than the hint is what stops a
    hallucinated dimension reaching a tool argument: the value that leaves here is
    always one the KPI definition actually lists.
    """
    if not hint:
        return None
    needle = _normalise(hint)
    for dimension in dimensions:
        if _normalise(dimension) == needle:
            return dimension
    for dimension in dimensions:
        if needle and (needle in _normalise(dimension) or _normalise(dimension) in needle):
            return dimension
    return None


def resolve(
    intent: Intent,
    *,
    dataset_id: uuid.UUID,
    dataset_name: str,
    choices: tuple[KpiChoice, ...],
    dimensions: tuple[str, ...],
    investigation_id: uuid.UUID | None = None,
    carried_kpi_definition_id: uuid.UUID | None = None,
) -> ResolvedContext:
    """Turn hints into ids, or explain why that is not possible."""
    assumptions: list[str] = list(intent.dropped_hints)

    if not choices:
        return ResolvedContext(
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            clarification=Clarification(
                code="NO_KPI_CONFIGURED",
                message=(
                    "This dataset has no KPI configured yet, so there is nothing to analyse. "
                    "Configure one to make it Analysis Ready, then ask again."
                ),
            ),
        )

    active = next((c for c in choices if c.is_active), None)
    chosen: KpiChoice | None = None
    source = HintSource.ACTIVE_DEFAULT

    if intent.kpi_hint:
        matched = match_kpis(intent.kpi_hint, choices)
        if len(matched) == 1:
            chosen, source = matched[0], HintSource.QUESTION
        elif len(matched) > 1:
            # Genuinely ambiguous. Stop rather than pick: picking the active one
            # here would answer a different question from the one asked, and the
            # reader would have no way to tell.
            return ResolvedContext(
                dataset_id=dataset_id,
                dataset_name=dataset_name,
                clarification=Clarification(
                    code="AMBIGUOUS_KPI",
                    message=(
                        f"{intent.kpi_hint!r} could mean more than one KPI on this dataset. "
                        "Which one should I analyse?"
                    ),
                    options=tuple(c.name for c in matched),
                ),
            )
        elif active is not None:
            # The hint matched nothing, but there is exactly one thing to analyse.
            # Answering it and saying so beats refusing.
            chosen, source = active, HintSource.ACTIVE_DEFAULT
            assumptions.append(
                f"You asked about {intent.kpi_hint!r}; this dataset's configured KPI is "
                f"{active.name}, so that is what was analysed."
            )

    if chosen is None and carried_kpi_definition_id is not None:
        carried = next(
            (c for c in choices if c.kpi_definition_id == carried_kpi_definition_id), None
        )
        if carried is not None:
            chosen, source = carried, HintSource.CARRIED_OVER

    if chosen is None:
        chosen = active
        source = HintSource.ACTIVE_DEFAULT

    if chosen is None:
        # Definitions exist but none is active - every one has been superseded.
        return ResolvedContext(
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            clarification=Clarification(
                code="NO_ACTIVE_KPI",
                message=(
                    "Every KPI definition on this dataset has been superseded, so there is no "
                    "active one to analyse. Configure a KPI and ask again."
                ),
                options=tuple(c.name for c in choices),
            ),
        )

    if intent.period_hint:
        # Recorded, never computed from. Reconciling it against the windows the
        # engine resolved happens in grounding, once those windows are known.
        assumptions.append(PERIOD_SUBSTITUTION_NOTE)

    dimension = match_dimension(intent.dimension_hint, dimensions)
    if intent.dimension_hint and dimension is None:
        assumptions.append(
            f"This dataset has no {intent.dimension_hint!r} dimension, so every configured "
            "dimension was analysed instead."
        )

    # A follow-up with nothing to follow up on is a fresh question. Silently
    # treating it as one would make the reply reference an investigation that does
    # not exist.
    if intent.kind is IntentKind.FOLLOW_UP_ANALYSIS and investigation_id is None:
        assumptions.append(
            "There was no earlier investigation to continue, so this was analysed from scratch."
        )

    return ResolvedContext(
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        kpi_definition_id=chosen.kpi_definition_id,
        kpi_name=chosen.name,
        kpi_source=source,
        dimension=dimension,
        # The segment is not validated here: whether "Cairo" exists is a property
        # of the analysed data, not of the schema, so the drill-down tool checks it
        # against the tree and reports honestly when it is absent.
        segment=intent.segment_hint,
        investigation_id=investigation_id,
        period_claim=intent.period_hint,
        assumptions=tuple(assumptions),
    )
