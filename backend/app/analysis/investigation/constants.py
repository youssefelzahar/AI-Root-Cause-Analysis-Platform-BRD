"""Wording and thresholds for the evidence layer.

Each value carries its justification, following the convention set by
``app.analysis.rca.constants``: a threshold nobody can defend is a threshold
nobody can tune. Nothing statistical lives here - the maths belongs to the RCA
and anomaly engines, and this module only decides how their findings are
described and how much weight they are given.
"""

from app.db.models.enums import EvidenceType

# Version of the evidence vocabulary itself: the claim wording, the confidence
# ladder, the stop-reason sentences. Bumped when a change would make two
# investigations' evidence read differently for identical numbers.
EVIDENCE_RULES_VERSION = "1.0"

# A node whose parent's children leave more than this much of the parent's
# movement unaccounted for is reported at MEDIUM rather than HIGH confidence. 5%
# is the same materiality floor the ranking uses, so "material enough to name as
# a driver" and "material enough to dent confidence" agree.
MAX_UNEXPLAINED_FOR_HIGH_CONFIDENCE = 0.05

# --- drill-down stop reasons -------------------------------------------------
# The engine emits eight codes; the specification names five categories. Both are
# kept: the code is what actually happened, the category is how it groups in a
# summary. Mapping rather than collapsing means nothing is invented and nothing
# is lost.

STOP_REASON_SENTENCES: dict[str, str] = {
    "max_depth_reached": "the maximum useful depth was reached",
    "no_dimensions_left": "every available dimension had already been used on this branch",
    "residual_bucket": (
        "this row is the grouped remainder of the smaller segments, so it has no single "
        "value to split by"
    ),
    "contribution_immaterial": (
        "its share of the change was below the materiality threshold for drilling further"
    ),
    "insufficient_rows": "there are too few rows behind it to say anything reliable about its parts",
    "branching_limit": "other segments at this level explained more of the change",
    "uniform_within_segment": (
        "no remaining dimension divided it into segments that behaved differently"
    ),
}

STOP_REASON_CATEGORIES: dict[str, str] = {
    "max_depth_reached": "maximum_useful_depth",
    "no_dimensions_left": "no_suitable_dimensions",
    "uniform_within_segment": "no_additional_explanatory_power",
    "residual_bucket": "no_suitable_dimensions",
    "contribution_immaterial": "remaining_contribution_below_threshold",
    "branching_limit": "remaining_contribution_below_threshold",
    "insufficient_rows": "insufficient_data",
}

# --- evidence-quality checks -------------------------------------------------
# Fixed order, so the checklist reads the same way on every investigation and two
# runs are comparable line by line.
CHECK_DATA_COVERAGE = "data_period_coverage"
CHECK_NUMERICAL_CONSISTENCY = "numerical_consistency"
CHECK_CONTRIBUTION_RECONCILIATION = "contribution_reconciliation"
CHECK_QUERY_PROVENANCE = "query_provenance"
CHECK_SOURCE_TRACEABILITY = "source_traceability"
CHECK_REQUIRED_METADATA = "required_metadata"

QUALITY_CHECK_ORDER = (
    CHECK_DATA_COVERAGE,
    CHECK_NUMERICAL_CONSISTENCY,
    CHECK_CONTRIBUTION_RECONCILIATION,
    CHECK_QUERY_PROVENANCE,
    CHECK_SOURCE_TRACEABILITY,
    CHECK_REQUIRED_METADATA,
)

# Coverage warnings describe the data, not the analysis, so they caveat a verdict
# without degrading it. The real airline dataset has 99.98% of its rows outside
# both compared windows and still deserves to read as VALIDATED: the analysis of
# the rows that do fall inside is sound. Quality fails on a broken identity or
# missing provenance - never on "the data is thin", which shows up instead as low
# confidence on the affected records and in the coverage evidence.
COVERAGE_ONLY_CHECKS = frozenset({CHECK_DATA_COVERAGE})

# Above this share of rows falling outside both compared windows, the coverage
# check warns. Half is the point past which "the periods describe this dataset"
# stops being a fair summary and the reader should be told.
MAX_ROWS_OUTSIDE_RATIO = 0.5

# Identities are checked to a relative tolerance rather than exactly: the values
# are floats that have been through SQL aggregation and Python arithmetic. This
# is loose enough to survive that and tight enough that a genuinely wrong number
# cannot hide behind it.
IDENTITY_RELATIVE_TOLERANCE = 1e-6
IDENTITY_ABSOLUTE_TOLERANCE = 1e-6

# --- analysis tools ----------------------------------------------------------
# What computed a number, named as the routine rather than as "duckdb": the
# query is recorded separately, and "which code produced this" and "which
# statement produced this" are different questions.
TOOL_PERIODS = "rca.period_analysis"
TOOL_CONTRIBUTION = "rca.contribution"
TOOL_RANKING = "rca.ranking"
TOOL_TREE = "rca.tree"
TOOL_ANOMALY = "anomaly.detectors"
TOOL_VALIDATION = "investigation.validation"
TOOL_EXECUTION = "investigation.execution"

# Types whose numbers were measured by a statement rather than derived from other
# evidence. Only these are required to carry query provenance; requiring it of a
# derived record would invite fabricating a plausible SELECT to satisfy the
# check, which is the one thing the query trace exists to prevent.
MEASURED_TYPES = frozenset(
    {
        EvidenceType.KPI_CHANGE,
        EvidenceType.DIMENSION_CHANGE,
        EvidenceType.CONTRIBUTION,
        EvidenceType.DRILL_DOWN,
        EvidenceType.NEW_SEGMENT,
        EvidenceType.GONE_SEGMENT,
        EvidenceType.OFFSETTING_FACTOR,
        EvidenceType.ANOMALY,
    }
)

# Types that must name a dimension and a segment value to mean anything.
SEGMENT_TYPES = frozenset(
    {
        EvidenceType.DIMENSION_CHANGE,
        EvidenceType.CONTRIBUTION,
        EvidenceType.DRILL_DOWN,
        EvidenceType.NEW_SEGMENT,
        EvidenceType.GONE_SEGMENT,
        EvidenceType.OFFSETTING_FACTOR,
    }
)

# Types that must name both periods.
PERIOD_TYPES = frozenset({EvidenceType.KPI_CHANGE, EvidenceType.COMPARISON})

# --- claim wording -----------------------------------------------------------
# The vocabulary rule from the PRD: a contribution is not a cause. "Contributed",
# "moved", "accounts for" are all defensible from the arithmetic; "caused" is
# not, and must never appear in a claim.
DIRECTION_VERBS = {
    "up": "increased",
    "down": "decreased",
    "flat": "did not change",
    "unknown": "could not be compared",
}

CLASSIFICATION_LABELS = {
    "primary": "primary driver",
    "secondary": "secondary driver",
    "offsetting": "offsetting factor",
    "immaterial": "immaterial contributor",
    "residual": "grouped remainder",
}

# Shown when a segment has no value for a dimension. A real SQL NULL is a genuine
# group and must not read as the string "None".
NULL_VALUE_LABEL = "(no value)"
