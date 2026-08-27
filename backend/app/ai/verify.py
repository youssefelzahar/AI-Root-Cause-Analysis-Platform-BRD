"""Step 7: did the answer state a number nobody measured?

The evidence layer made *never fabricate SQL* mechanical: a record's query must be
byte-identical to a statement in the trace, so a plausible-looking SELECT that never
ran fails a check rather than a review. This module does the same thing for the
model's prose. Every figure in the answer must appear in the bundle, or the answer
is discarded in favour of a template built from the bundle.

That is the difference between a prompt rule and a guarantee. The prompt asks the
model not to invent numbers; this refuses to publish it when it did.

Two deliberate leniencies, because a check that fires on correct answers gets
switched off:

* **Small integers are not checked.** "the top 3 drivers", "both periods", "one
  segment" are ordinary prose. Any figure that could be a measured value is well
  above the floor.
* **Rounding is allowed.** The model is asked to write for a reader, so 23.077
  becoming "23.1%" must pass. A relative tolerance absorbs that; an invented figure
  is nowhere near a real one.
"""

import re
from dataclasses import dataclass

from app.ai.constants import (
    CAUSAL_PHRASES,
    MIN_VERIFIABLE_MAGNITUDE,
    VERIFY_RELATIVE_TOLERANCE,
)
from app.ai.models import EvidenceBundle
from app.core.logging import get_logger

logger = get_logger(__name__)

# Numbers as a reader writes them: optional sign, thousands separators, decimals,
# an optional trailing percent. The percent sign is captured so "23.1%" can be
# compared against a percentage rather than against a raw value.
_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?\s*%?")

# Four-digit runs that are years rather than measurements. A period label contains
# them and the answer is meant to quote it.
_YEAR = re.compile(r"^(19|20)\d{2}$")

# An ISO date is removed before any number is looked for, because its parts scan as
# signed numbers: "2026-06-01" yields -6 and -1, and a period label is full of them.
# The answer is explicitly told to quote the periods, so rejecting it for doing so
# would fire the guard on every correct answer.
_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?(?:[T ]\d{2}:\d{2}(?::\d{2})?)?")


@dataclass(frozen=True)
class Verdict:
    """Whether the prose may be published, and what was wrong with it."""

    grounded: bool
    ungrounded: tuple[str, ...] = ()
    checked: int = 0

    @property
    def detail(self) -> str:
        if self.grounded:
            return f"All {self.checked} figure(s) in the answer appear in the evidence."
        return (
            "The answer quoted figures the evidence does not contain: "
            + ", ".join(self.ungrounded)
        )


def _parse(token: str) -> tuple[float, bool] | None:
    """A token as ``(value, was_a_percentage)``, or None if it is not a number."""
    text = token.strip()
    is_percent = text.endswith("%")
    text = text.rstrip("%").strip().replace(",", "")
    if not text or text in {"-", "+"}:
        return None
    if _YEAR.match(text.lstrip("+-")):
        return None
    try:
        return float(text), is_percent
    except ValueError:
        return None


def _matches(value: float, candidates: tuple[float, ...]) -> bool:
    """Whether a stated figure is one of the measured ones, allowing for rounding.

    Magnitudes are compared, not signed values: the evidence records a change of
    -300 and an answer may legitimately write "decreased by 300". The direction is
    carried by the words, and the bundle's own ``direction`` field is what the
    prompt tells the model to use for it.
    """
    for candidate in candidates:
        scale = max(abs(value), abs(candidate), 1.0)
        if abs(abs(value) - abs(candidate)) <= VERIFY_RELATIVE_TOLERANCE * scale:
            return True
        # A share written as 0.8 when the bundle holds 80.0, or the reverse.
        if abs(abs(value) * 100.0 - abs(candidate)) <= VERIFY_RELATIVE_TOLERANCE * scale * 100.0:
            return True
    return False


def causal_phrases(answer: str) -> tuple[str, ...]:
    """Wording that asserts causation rather than contribution.

    The house rule, from ``analysis.investigation.constants``: *contributed*,
    *moved* and *accounts for* are defensible from the arithmetic; *caused* is not.
    Checking it here makes it mechanical, the same way the numeric guard does for
    figures.

    "Driver" and "drove" are deliberately absent from the list. This codebase names
    its ranked segments *drivers* throughout, so banning the word would reject the
    platform's own vocabulary.
    """
    lowered = f" {(answer or '').lower()} "
    return tuple(phrase for phrase in CAUSAL_PHRASES if phrase in lowered)


def check(answer: str, bundle: EvidenceBundle) -> Verdict:
    """Every figure in the prose, against every figure in the bundle."""
    candidates = bundle.numbers()
    ungrounded: list[str] = []
    checked = 0

    # Dates first, whole, so their components never reach the number scanner.
    scannable = _DATE.sub(" ", answer or "")

    for token in _NUMBER.findall(scannable):
        parsed = _parse(token)
        if parsed is None:
            continue
        value, _ = parsed
        if abs(value) < MIN_VERIFIABLE_MAGNITUDE:
            continue
        checked += 1
        if not _matches(value, candidates):
            ungrounded.append(token.strip())

    if ungrounded:
        # The figures are logged, not the answer: prose is unredacted and a
        # question can name a customer.
        logger.warning(
            "ai_answer_rejected_ungrounded_numbers",
            extra={
                "investigation_id": str(bundle.investigation_id),
                "ungrounded_count": len(ungrounded),
                "checked": checked,
            },
        )
        return Verdict(grounded=False, ungrounded=tuple(ungrounded), checked=checked)

    return Verdict(grounded=True, checked=checked)
