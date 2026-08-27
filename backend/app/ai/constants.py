"""Recipes, thresholds and wording for the AI analyst.

Following the convention set by ``app.analysis.rca.constants``: every value
carries its justification, because a threshold nobody can defend is a threshold
nobody can tune. Nothing statistical lives here - the AI layer computes no
analysis, so the only numbers are text-handling bounds and the plan recipes.

Operational caps that an operator might genuinely need to change live in
``app.core.config`` instead. The split is the same one the analysis packages use.
"""

from app.ai.models import IntentKind

# Version of the AI layer's vocabulary: the intent set, the plan recipes, the
# claim wording. Bumped when a change would make two answers to the same question
# read differently for identical evidence. Reported on every response so a stored
# transcript can be told apart from a current one.
ANALYST_RULES_VERSION = "1.0"

# --- tool names ---------------------------------------------------------------
# Declared as constants rather than bare strings so a recipe naming a tool that
# does not exist fails at import, not at request time.
TOOL_GET_KPI_RESULT = "get_kpi_result"
TOOL_DETECT_ANOMALY = "detect_anomaly"
TOOL_DIMENSION_ANALYSIS = "dimension_analysis"
TOOL_CONTRIBUTION_ANALYSIS = "contribution_analysis"
TOOL_DRILL_DOWN = "drill_down"
TOOL_GET_INVESTIGATION = "get_investigation"
TOOL_GET_EVIDENCE = "get_evidence"

# --- plan recipes -------------------------------------------------------------
# One fixed sequence per intent, rather than a plan the model writes. Two reasons.
# The analysis is a single pass whatever we ask for - one investigation computes
# every dimension and the tree together - so there is nothing for a model-authored
# plan to optimise. And a recipe is inspectable: a reader can see what a question
# will do before it runs.
#
# The recipes form a lattice on purpose. ROOT_CAUSE_ANALYSIS is a superset of
# KPI_ANALYSIS, CONTRIBUTION_ANALYSIS and DRILL_DOWN, so a misclassified intent
# still produces a usable answer with some steps that had nothing to report -
# never a wrong one. That matters because a small model does misclassify.
PLAN_RECIPES: dict[IntentKind, tuple[str, ...]] = {
    IntentKind.KPI_ANALYSIS: (
        TOOL_GET_KPI_RESULT,
    ),
    IntentKind.ROOT_CAUSE_ANALYSIS: (
        TOOL_GET_KPI_RESULT,
        TOOL_DIMENSION_ANALYSIS,
        TOOL_CONTRIBUTION_ANALYSIS,
        TOOL_DRILL_DOWN,
        TOOL_GET_EVIDENCE,
    ),
    IntentKind.ANOMALY_ANALYSIS: (
        TOOL_GET_KPI_RESULT,
        TOOL_DETECT_ANOMALY,
    ),
    IntentKind.DIMENSION_ANALYSIS: (
        TOOL_GET_KPI_RESULT,
        TOOL_DIMENSION_ANALYSIS,
    ),
    IntentKind.CONTRIBUTION_ANALYSIS: (
        TOOL_GET_KPI_RESULT,
        TOOL_CONTRIBUTION_ANALYSIS,
        TOOL_GET_EVIDENCE,
    ),
    # Contribution is included even though the question is about one segment: it is
    # a projection of the same investigation, so it costs nothing, and without it
    # the answer has no idea whether the named segment is the main driver or a
    # footnote - which is most of what "what happened in Cairo" is asking.
    IntentKind.DRILL_DOWN: (
        TOOL_GET_KPI_RESULT,
        TOOL_CONTRIBUTION_ANALYSIS,
        TOOL_DRILL_DOWN,
        TOOL_GET_EVIDENCE,
    ),
    IntentKind.INVESTIGATION_SUMMARY: (
        TOOL_GET_INVESTIGATION,
        TOOL_CONTRIBUTION_ANALYSIS,
        TOOL_GET_EVIDENCE,
    ),
    # A follow-up carries the previous investigation, so it reads rather than
    # recomputes. Which slice it reads depends on what the follow-up named, and
    # the planner narrows this at plan time.
    IntentKind.FOLLOW_UP_ANALYSIS: (
        TOOL_GET_INVESTIGATION,
        TOOL_GET_KPI_RESULT,
        TOOL_CONTRIBUTION_ANALYSIS,
        TOOL_DRILL_DOWN,
        TOOL_GET_EVIDENCE,
    ),
}

# --- hint sanitisation --------------------------------------------------------
# A schema-constrained small model emits the *string* "null" rather than a JSON
# null often enough that coercing it is mandatory, not defensive. Measured, not
# assumed: 5 of 9 test questions leaked one before the few-shot examples were
# added, and a hint of "null" would otherwise be matched against the KPI list.
NULL_LIKE_HINTS = frozenset({"", "null", "none", "n/a", "na", "nil", "unknown", "-"})

# A hint is meant to be words copied out of the question. Past this length it is
# almost always the whole question echoed back, which matched no KPI and produced
# a spurious clarification. Well above any real metric or segment name.
MAX_HINT_CHARS = 40

# --- answer verification ------------------------------------------------------
# Numbers below this magnitude are not checked against the evidence bundle. Small
# integers appear in ordinary prose - "the top 3 drivers", "both periods", "one
# segment" - and demanding a source for them would reject correct answers. Any
# figure that could be a measured value is well above it.
MIN_VERIFIABLE_MAGNITUDE = 3.0

# How close a number in the answer must be to one in the bundle to count as the
# same number. Generous because the model is asked to round for readability: 0.5%
# absorbs "23.1%" for 23.077 and "1,200" for 1200.0, while a genuinely invented
# figure is nowhere near a real one.
VERIFY_RELATIVE_TOLERANCE = 0.005

# --- wording ------------------------------------------------------------------
# The same vocabulary rule the evidence layer enforces. A contribution is not a
# cause, and the explanation prompt is not the only thing standing between the
# model and the word "caused" - the template fallback must obey it too.
CAUSAL_CLAIM_CAVEAT = (
    "This describes contribution to a measured change, not proven causation."
)

# Wording that turns a contribution into a causal claim. Checked against generated
# prose, which is what makes the rule mechanical rather than a request in a prompt.
# Padded with spaces at the check site so "because" does not match inside a word.
#
# "driver" and "drove" are deliberately absent: this codebase calls its ranked
# segments drivers throughout, so banning the word would reject its own vocabulary.
CAUSAL_PHRASES = (
    "caused",
    "causing",
    "the cause of",
    "due to",
    "because of",
    "as a result of",
    "responsible for",
)

# Shown when a segment has no value for a dimension, matching
# ``app.analysis.investigation.constants.NULL_VALUE_LABEL`` so one segment is not
# named two ways across the two layers.
NULL_VALUE_LABEL = "(no value)"

# Read back to the user when the question named a period the engine could not
# target. The substitution is stated rather than silently applied: the periods are
# anchored on the data's own latest timestamp, so a dataset's newest complete
# period frequently is not the one a reader had in mind.
PERIOD_SUBSTITUTION_NOTE = (
    "The comparison windows come from this dataset's own latest complete period "
    "and the KPI's comparison setting, so a period named in a question is "
    "reported against rather than analysed directly."
)
