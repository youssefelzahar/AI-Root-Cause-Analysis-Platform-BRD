"""A deterministic classifier for questions, with no model involved.

Serves three purposes, which is why it is worth its own module rather than being
buried in the intent code:

* It is the **fallback** when the model is unavailable. A question can still be
  routed to the right analysis and answered from a template, which is what makes
  "Ollama is down but the investigation completed" a real outcome rather than a
  slogan.
* It is the **fake provider's** implementation, so the test suite exercises the
  whole pipeline without a model installed.
* It is a **sanity check** on the model. Where the two disagree the disagreement is
  recorded; where the model returns something absurd the keyword answer is used.

It is deliberately crude. English question openers map onto intents with high
precision - "why" really does mean root cause, "which" really does mean
contribution - and a crude rule that can be read in ten seconds beats a clever one
nobody can predict. Where it has no opinion it says so, and the model's answer
stands.
"""

import re
from dataclasses import dataclass

from app.ai.models import IntentKind

# --- period words -------------------------------------------------------------
# Recognised only to *echo back* what the question named. The engines anchor
# periods on the data's own latest timestamp, so nothing here is ever parsed into
# a date - see ``ResolvedContext.period_claim``.
MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)

_RELATIVE_PERIODS = (
    "last month", "last quarter", "last year", "last week",
    "this month", "this quarter", "this year", "this week",
    "previous month", "previous quarter", "previous year",
    "year over year", "month over month",
)

_QUARTERS = ("q1", "q2", "q3", "q4")

# --- intent cues --------------------------------------------------------------
# Ordered by specificity, and the order matters: "why was July anomalous" is an
# anomaly question, not a root-cause one, so the anomaly cue has to be tested
# first even though "why" also matches.
_ANOMALY_CUES = (
    "anomal", "unusual", "outlier", "spike", "spiked", "dip", "abnormal",
    "out of the ordinary", "unexpected",
)
_SUMMARY_CUES = ("summar", "recap", "overview of the investigation", "what did you find")
_CONTRIBUTION_CUES = (
    "which", "who", "biggest driver", "largest driver", "main driver",
    "top driver", "contributed most", "contributor", "responsible for",
    "accounted for", "most of the",
)
_DIMENSION_CUES = ("breakdown", "by dimension")

# "Break revenue down by region" puts the metric between the two halves of the
# cue, so a phrase list cannot see it. The captured group is the dimension.
_BREAKDOWN_PATTERN = re.compile(
    r"\b(?:break|split|group|segment)\s+(?:[\w\s]{0,30}?\s+)?"
    r"(?:down\s+)?(?:by|per)\s+([\w\s]+?)\s*[?.!]?$",
    re.I,
)
# "broken down by region", "grouped by channel".
_BROKEN_DOWN_PATTERN = re.compile(
    r"\b(?:broken\s+down|split|grouped|segmented)\s+(?:by|per)\s+([\w\s]+?)\s*[?.!]?$",
    re.I,
)
_CAUSE_CUES = ("why", "what caused", "what drove", "reason for", "explain the decline", "explain the drop")
_VALUE_CUES = ("what is", "what was", "how much", "compare", "current value", "show me")

# "What happened in Cairo?" - a segment question. The captured group is a claim
# about the question, validated against real segment values before it is used.
_SEGMENT_PATTERNS = (
    re.compile(r"\bwhat(?:'s| is| was)? happen(?:ed|ing)? (?:in|for|with|at) ([\w\s&'-]+?)\s*[?.!]?$", re.I),
    re.compile(r"\b(?:drill|zoom|dig) (?:in|into|down into) ([\w\s&'-]+?)\s*[?.!]?$", re.I),
    re.compile(r"\bwhat about ([\w\s&'-]+?)\s*[?.!]?$", re.I),
    re.compile(r"\btell me (?:more )?about ([\w\s&'-]+?)\s*[?.!]?$", re.I),
)

# A metric named right after a question opener: "why did *revenue* drop".
_KPI_PATTERN = re.compile(
    r"\b(?:did|do|does|is|was|are|were|has|have)\s+(?:the\s+)?([a-z][\w\s]{0,28}?)\s+"
    r"(?:decrease|decreas|increase|increas|drop|dropp|fall|fell|rise|ros|grow|grew|"
    r"declin|chang|move|go|went|spike|dip)",
    re.I,
)


@dataclass(frozen=True)
class KeywordGuess:
    """What the rules could tell from the words alone.

    ``confident`` is False when no cue matched and the intent is a default rather
    than a reading. The caller uses it to decide whether to override the model.
    """

    kind: IntentKind
    kpi_hint: str | None = None
    period_hint: str | None = None
    dimension_hint: str | None = None
    segment_hint: str | None = None
    confident: bool = False


def _contains(text: str, cues: tuple[str, ...]) -> bool:
    return any(cue in text for cue in cues)


def period_in(question: str) -> str | None:
    """The period the question named, verbatim, or None.

    Verbatim because it is quoted back to the user. Normalising "Jul" to "2026-07"
    would be date arithmetic, which this layer does not do.
    """
    lowered = question.lower()
    for phrase in _RELATIVE_PERIODS:
        if phrase in lowered:
            return phrase
    for month in MONTHS:
        if re.search(rf"\b{month}\b", lowered):
            # Keep a following year if the question gave one: "July 2026".
            match = re.search(rf"\b{month}\b(\s+\d{{4}})?", question, re.I)
            return match.group(0).strip() if match else month
    for quarter in _QUARTERS:
        if re.search(rf"\b{quarter}\b", lowered):
            match = re.search(rf"\b{quarter}\b(\s+\d{{4}})?", question, re.I)
            return match.group(0).strip().upper() if match else quarter.upper()
    match = re.search(r"\b(20\d{2}-\d{2}(?:-\d{2})?)\b", question)
    if match:
        return match.group(1)
    return None


def _kpi_in(question: str) -> str | None:
    match = _KPI_PATTERN.search(question)
    if match is None:
        return None
    candidate = match.group(1).strip()
    # "did it drop" and friends carry no metric.
    if candidate.lower() in {"it", "this", "that", "they", "we", "there"}:
        return None
    return candidate


def _dimension_in(question: str) -> str | None:
    """The dimension a breakdown question named, or None."""
    for pattern in (_BROKEN_DOWN_PATTERN, _BREAKDOWN_PATTERN):
        match = pattern.search(question)
        if match is not None:
            return match.group(1).strip()
    return None


def _segment_in(question: str) -> str | None:
    for pattern in _SEGMENT_PATTERNS:
        match = pattern.search(question)
        if match is None:
            continue
        candidate = match.group(1).strip()
        # A trailing period word is a period question, not a segment one:
        # "what happened in July" must not resolve Cairo-style.
        if candidate.lower() in MONTHS or candidate.lower() in _RELATIVE_PERIODS:
            return None
        return candidate
    return None


def classify(question: str) -> KeywordGuess:
    """Read the question with rules only.

    Cue order encodes precedence: a question can contain both "why" and "anomaly",
    and the more specific reading wins.
    """
    text = (question or "").strip()
    lowered = text.lower()
    period = period_in(text)
    kpi = _kpi_in(text)
    segment = _segment_in(text)
    dimension = _dimension_in(text)

    if _contains(lowered, _ANOMALY_CUES):
        return KeywordGuess(
            IntentKind.ANOMALY_ANALYSIS,
            kpi_hint=kpi,
            period_hint=period,
            confident=True,
        )
    if _contains(lowered, _SUMMARY_CUES):
        return KeywordGuess(IntentKind.INVESTIGATION_SUMMARY, confident=True)
    if dimension is not None or _contains(lowered, _DIMENSION_CUES):
        return KeywordGuess(
            IntentKind.DIMENSION_ANALYSIS,
            kpi_hint=kpi,
            period_hint=period,
            dimension_hint=dimension,
            confident=True,
        )
    if _contains(lowered, _CONTRIBUTION_CUES):
        return KeywordGuess(
            IntentKind.CONTRIBUTION_ANALYSIS,
            kpi_hint=kpi,
            period_hint=period,
            dimension_hint=dimension,
            confident=True,
        )
    if segment is not None:
        return KeywordGuess(
            IntentKind.DRILL_DOWN,
            period_hint=period,
            segment_hint=segment,
            confident=True,
        )
    if _contains(lowered, _CAUSE_CUES):
        return KeywordGuess(
            IntentKind.ROOT_CAUSE_ANALYSIS,
            kpi_hint=kpi,
            period_hint=period,
            confident=True,
        )
    if _contains(lowered, _VALUE_CUES):
        return KeywordGuess(
            IntentKind.KPI_ANALYSIS,
            kpi_hint=kpi,
            period_hint=period,
            confident=True,
        )

    # No cue matched. Root cause is the default because it is the superset recipe:
    # an unreadable question still gets the full picture rather than a thin slice.
    return KeywordGuess(
        IntentKind.ROOT_CAUSE_ANALYSIS,
        kpi_hint=kpi,
        period_hint=period,
        segment_hint=segment,
        confident=False,
    )
