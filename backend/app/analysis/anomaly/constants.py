"""Every named threshold the anomaly engine depends on, in one place.

Each value is justified in a comment. A threshold nobody can defend is a
threshold nobody can tune, and PRD principle 6 requires analytical claims to be
traceable - including the claim "this period is unusual".
"""

# --- degeneracy guards ------------------------------------------------------

# Below this, a float is treated as zero. Values reaching the engine have been
# through a DOUBLE cast, so exact equality is not safe. Same value and same
# reasoning as the RCA engine's guard.
ABS_EPSILON = 1e-9

# A dispersion is "no dispersion" relative to the values it came from, never at
# a fixed absolute size. A KPI held in fractions can legitimately have a MAD of
# 1e-10, and an absolute floor of ABS_EPSILON would declare that series
# perfectly flat and cap every score. Judged against the baseline's own
# magnitude, the same ratio means the same thing whether the KPI is measured in
# billions or in parts per million.
SCALE_RELATIVE_EPSILON = 1e-9


# --- baseline ---------------------------------------------------------------

# How many prior periods the rolling baseline looks back over. Twelve is one
# year of monthly reporting: long enough for the median to be stable, short
# enough that a genuine level shift from two years ago is not still counted as
# "normal". The window is strictly trailing - a point is never part of the
# baseline it is judged against, and no future point ever informs it.
BASELINE_WINDOW = 12

# Fewer non-missing priors than this and no score is produced at all. The
# modified z-score needs enough points for the median and the MAD to mean
# something; below five, one value moves the median and the whole scale with it.
# Deliberately not higher: a seven-period series (the spec's own example) must
# still be scoreable at the end.
MIN_BASELINE_OBSERVATIONS = 5


# --- scoring ----------------------------------------------------------------

# 1 / 1.4826. For normally distributed data 1.4826 * MAD is a consistent
# estimator of the standard deviation, so multiplying the deviation-over-MAD
# ratio by this constant puts the score on the same scale as an ordinary
# z-score: "how many robust standard deviations from normal".
MAD_TO_SIGMA = 0.6745

# sqrt(2/pi) = 1 / 1.2533. Iglewicz & Hoaglin give this second form of the
# modified z-score for exactly the case the MAD cannot handle - more than half
# the window sharing one value, which makes the MAD zero. E|X-mu| = sigma *
# sqrt(2/pi), so the mean absolute deviation estimates sigma on the same scale
# and the score keeps its meaning across the fallback. Using MAD_TO_SIGMA here
# instead would under-report every score by ~15%.
MEAN_AD_TO_SIGMA = 0.7979

# A score this large stops being informative - the point is simply far away, and
# reporting 4,000 sigma implies a precision the estimate does not have. Capped
# rather than dropped so the ordering between extreme points is still stable.
MAX_ANOMALY_SCORE = 1_000.0


# --- severity ---------------------------------------------------------------

# Iglewicz & Hoaglin's published cutoff for the modified z-score (NIST/SEMATECH
# e-Handbook 1.3.5.17). This is the one genuinely conventional number here, and
# it is what separates NORMAL from an anomaly - not an invented threshold.
ANOMALY_THRESHOLD = 3.5

# Bands above the cutoff. Roughly geometric so each step is a visibly bigger
# departure rather than an arbitrary slice: at 12 sigma a normal process would
# not produce the observation in the lifetime of the universe, which is why that
# is where CRITICAL starts.
LOW_THRESHOLD = ANOMALY_THRESHOLD
MEDIUM_THRESHOLD = 5.0
HIGH_THRESHOLD = 8.0
CRITICAL_THRESHOLD = 12.0


# --- IQR detector -----------------------------------------------------------

# Tukey's fence multiplier. 1.5 * IQR is the standard "outlier" fence and the
# same one the profiler already uses for column-level outlier counts, so the two
# features do not disagree about what an outlier is.
IQR_FENCE_MULTIPLIER = 1.5


# --- series bounds ----------------------------------------------------------

# Safety cap on periods pulled back from one series query. A daily KPI over
# twenty years is ~7,300 points, which is fine; anything past this is either a
# mis-detected grain or a timestamp column being read as a date, and returning
# 100,000 points to a chart helps nobody. The most recent periods are kept.
MAX_PERIODS = 5_000

# Used when the profiler could not determine a reporting frequency. Monthly is
# the reporting grain a business KPI is most often read at, and the assumption is
# always surfaced as a notice rather than made silently.
DEFAULT_GRAIN = "month"


# --- model fit --------------------------------------------------------------

# Both diagnostics below are claims about the *shape* of the series, and a claim
# about shape needs a shape to look at. With two evaluated periods one anomaly is
# a 50% "anomaly rate" and one drop is a "trend"; neither statement means
# anything. Below this many evaluated periods the engine says nothing about fit.
MIN_PERIODS_FOR_FIT_DIAGNOSTICS = 12

# A trailing baseline assumes the series has no repeating cycle. When this share
# of evaluated periods is flagged, that assumption has failed - a weekly cycle
# on daily data flags every weekend, roughly one period in six. Past this point
# the honest thing is to say the model does not fit rather than to hand back a
# list of "anomalies" that are just Saturdays.
HIGH_ANOMALY_RATE = 0.15

# When the baseline's own drift across the series exceeds this multiple of its
# dispersion, the series is trending. A trailing baseline then lags behind it,
# which biases the two directions unequally, so the report says so. Two rather
# than one: at one, a single large anomaly inside a half is enough to move that
# half's median past the bar and report a trend that is not there.
TRENDING_DRIFT_RATIO = 2.0
