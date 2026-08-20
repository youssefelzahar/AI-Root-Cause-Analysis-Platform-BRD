"""Every named threshold the RCA engine depends on, in one place.

Each value is justified in a comment. A threshold nobody can defend is a
threshold nobody can tune, and PRD principle 6 requires analytical claims to be
traceable - including the claim "this segment is a driver".
"""

# --- degeneracy guards ------------------------------------------------------

# Below this, a float is treated as zero. Values reaching the engine have been
# through a DOUBLE cast, so exact equality is not safe.
ABS_EPSILON = 1e-9

# When |net change| / gross movement falls below this, the net change is too
# small to divide by: with both signs present, max|delta_j| >= gross/2, so a
# net-basis contribution would be at least 1/(2*0.05) = 1000%. Past roughly that
# point the ratio has stopped being a share and become a division artifact, so
# the engine switches to reporting shares of gross movement instead.
NET_TO_GROSS_MIN_RATIO = 0.05

# Contributions must sum to 1. Anything beyond this is a lost-rows bug, not
# float noise, and is surfaced rather than normalised away.
CONTRIBUTION_SUM_TOLERANCE = 1e-6


# --- classification ---------------------------------------------------------

# The 80/20 convention. It is the one genuinely conventional number here, so it
# is echoed back in the API response rather than hidden inside the engine.
PARETO_TARGET = 0.80

# A segment moving less than this share of the total is indistinguishable from
# proportional drift and is not worth naming.
MIN_MATERIAL_CONTRIBUTION = 0.05

# A "root cause" list longer than the KPI's own dimension limit is a
# distribution, not a diagnosis. Matches validation_service.MAX_DIMENSIONS.
MAX_PRIMARY_DRIVERS = 5

# Below this, every cell moved in proportion to its baseline share, so the
# dimension carries no information about where the change came from.
MIN_EXPLANATORY_POWER = 0.10


# --- drill-down -------------------------------------------------------------

MAX_TREE_DEPTH = 3

# Nodes expanded at depth 1, 2, 3. Deeper levels are less certain and less
# actionable, so they branch less: 3 + 3*2 + 6*2 = 21 nodes worst case.
BRANCHING_BY_DEPTH = (3, 2, 2)

# A node that is not material does not deserve a query level of its own.
MIN_CONTRIBUTION_TO_DRILL = MIN_MATERIAL_CONTRIBUTION


# --- support ----------------------------------------------------------------

# For SUM/COUNT the decomposition is an accounting identity, not an estimate, so
# there is deliberately no row-count gate: three rows that moved the total by
# 40% genuinely did, and one large contract really can be the root cause.
# For mean-like aggregations a group average over few rows is unstable, so these
# two apply to AVG and MEDIAN only.
MIN_ROWS_FOR_MEAN_STABILITY = 30  # conventional CLT rule of thumb
MIN_ROWS_TO_DRILL = 10


# --- truncation -------------------------------------------------------------

# A display bound, not a statistical one: past ~50 rows no dimension table is
# legible. The residual bucket keeps the arithmetic exact regardless.
MAX_VALUES_PER_DIMENSION = 50

# Safety cap on rows pulled back from one breakdown query.
MAX_SEGMENTS_SCANNED = 5_000

# Label for the synthetic bucket holding everything past the truncation limit.
OTHER_BUCKET = "(other)"


# --- severity ---------------------------------------------------------------

# Carried over verbatim from the pre-Phase-1 engine so the UI's severity
# vocabulary is unchanged.
CRITICAL_THRESHOLD = 25.0
HIGH_THRESHOLD = 12.0
MEDIUM_THRESHOLD = 5.0
