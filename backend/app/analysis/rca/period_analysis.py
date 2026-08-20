"""Resolve a KPI comparison into two concrete date windows.

Nothing in Phase 1 interprets ``comparison`` or ``comparison_config`` - they are
stored and never read. All of this is new.

Two rules run through the module:

* Windows are half-open ``[start, end)``. ``t <= '2026-07-31'`` drops every row
  at ``2026-07-31 09:15`` once the time column is a TIMESTAMP, which it usually
  is after casting.
* The anchor is the data's own maximum timestamp, never the wall clock, so an
  investigation is reproducible from the file alone.
"""

from datetime import datetime, timedelta
from typing import Any

from app.analysis.rca.models import Grain, Period, PeriodResolution
from app.core.exceptions import ValidationError
from app.db.models.enums import ComparisonPeriod

# Profiler frequency -> calendar grain. The profiler already returns None below
# 0.6 confidence, so an absent entry means "we genuinely do not know".
FREQUENCY_TO_GRAIN: dict[str, Grain] = {
    "daily": Grain.DAY,
    "weekly": Grain.WEEK,
    "monthly": Grain.MONTH,
    "quarterly": Grain.QUARTER,
    "yearly": Grain.YEAR,
}

COMPARISON_TO_GRAIN: dict[ComparisonPeriod, Grain] = {
    ComparisonPeriod.PREVIOUS_MONTH: Grain.MONTH,
    ComparisonPeriod.PREVIOUS_QUARTER: Grain.QUARTER,
    ComparisonPeriod.PREVIOUS_YEAR: Grain.YEAR,
}


def _add_months(moment: datetime, months: int) -> datetime:
    """Calendar month arithmetic without pulling in dateutil.

    ``dateutil`` is only present transitively through pandas, so relying on it
    would be an undeclared dependency.
    """
    total = moment.month - 1 + months
    year = moment.year + total // 12
    month = total % 12 + 1
    return moment.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def bucket_start(moment: datetime, grain: Grain) -> datetime:
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if grain is Grain.DAY:
        return midnight
    if grain is Grain.WEEK:
        # ISO-8601: weeks start on Monday. Sunday-start is a real business
        # convention elsewhere, so this is stated rather than assumed.
        return midnight - timedelta(days=midnight.weekday())
    if grain is Grain.MONTH:
        return midnight.replace(day=1)
    if grain is Grain.QUARTER:
        first_month = 3 * ((midnight.month - 1) // 3) + 1
        return midnight.replace(month=first_month, day=1)
    if grain is Grain.YEAR:
        return midnight.replace(month=1, day=1)
    raise ValueError(f"bucket_start is undefined for grain {grain}")


def bucket_end(start: datetime, grain: Grain) -> datetime:
    if grain is Grain.DAY:
        return start + timedelta(days=1)
    if grain is Grain.WEEK:
        return start + timedelta(days=7)
    if grain is Grain.MONTH:
        return _add_months(start, 1)
    if grain is Grain.QUARTER:
        return _add_months(start, 3)
    if grain is Grain.YEAR:
        return start.replace(year=start.year + 1)
    raise ValueError(f"bucket_end is undefined for grain {grain}")


def shift_bucket(start: datetime, grain: Grain, periods: int) -> datetime:
    if grain is Grain.DAY:
        return start + timedelta(days=periods)
    if grain is Grain.WEEK:
        return start + timedelta(days=7 * periods)
    if grain is Grain.MONTH:
        return _add_months(start, periods)
    if grain is Grain.QUARTER:
        return _add_months(start, 3 * periods)
    if grain is Grain.YEAR:
        return start.replace(year=start.year + periods)
    raise ValueError(f"shift_bucket is undefined for grain {grain}")


def resolve_grain(comparison: ComparisonPeriod, detected_frequency: str | None) -> Grain:
    """Pick the bucket size for this comparison.

    ``previous_period`` means "one of whatever period this data is reported in",
    so it defers to the profiler's detected frequency. When that is unknown the
    caller falls back to an equal-span split rather than inventing a month.
    """
    if comparison in COMPARISON_TO_GRAIN:
        return COMPARISON_TO_GRAIN[comparison]
    if comparison is ComparisonPeriod.CUSTOM:
        return Grain.CUSTOM
    grain = FREQUENCY_TO_GRAIN.get((detected_frequency or "").lower())
    return grain if grain is not None else Grain.EQUAL_SPAN


def _parse_config_datetime(config: dict[str, Any], key: str) -> datetime | None:
    raw = config.get(key)
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError as exc:
        raise ValidationError(
            f"comparison_config.{key} is not a valid ISO date.",
            code="RCA_CUSTOM_PERIOD_INVALID",
            details={"field": key, "value": str(raw)},
        ) from exc


def _resolve_custom(config: dict[str, Any] | None, anchor: datetime) -> PeriodResolution:
    """Explicit windows from ``comparison_config``.

    This blob has had no schema and no consumer until now; the shape is defined
    here. ``current_end`` is inclusive on the wire (users think in whole days)
    and converted to the half-open form internally.
    """
    config = config or {}
    current_start = _parse_config_datetime(config, "current_start")
    current_end = _parse_config_datetime(config, "current_end")
    if current_start is None or current_end is None:
        raise ValidationError(
            "A custom comparison needs current_start and current_end in comparison_config.",
            code="RCA_CUSTOM_PERIOD_INVALID",
        )

    current_end_exclusive = current_end + timedelta(days=1)
    if current_end_exclusive <= current_start:
        raise ValidationError(
            "comparison_config.current_end must not be before current_start.",
            code="RCA_CUSTOM_PERIOD_INVALID",
        )

    previous_start = _parse_config_datetime(config, "previous_start")
    previous_end = _parse_config_datetime(config, "previous_end")
    if previous_start is None or previous_end is None:
        # Derive the immediately preceding window of equal length.
        length = current_end_exclusive - current_start
        previous_start = current_start - length
        previous_end_exclusive = current_start
    else:
        previous_end_exclusive = previous_end + timedelta(days=1)
        if previous_end_exclusive <= previous_start:
            raise ValidationError(
                "comparison_config.previous_end must not be before previous_start.",
                code="RCA_CUSTOM_PERIOD_INVALID",
            )

    if previous_end_exclusive > current_start:
        raise ValidationError(
            "The custom comparison windows overlap; rows would be counted in both periods.",
            code="RCA_CUSTOM_PERIOD_INVALID",
        )

    return PeriodResolution(
        current=Period("current", current_start, current_end_exclusive),
        previous=Period("previous", previous_start, previous_end_exclusive),
        grain=Grain.CUSTOM,
        strategy="explicit",
        anchor=anchor,
    )


def _resolve_equal_span(min_ts: datetime, max_ts: datetime) -> PeriodResolution:
    """Halve the observed range when no reporting frequency could be detected.

    The profiler already tried to find a modal gap and failed, so guessing a
    calendar grain here would be fabrication. Splitting in half is
    deterministic, uses all the data, and is disclosed through ``grain``.
    """
    end_exclusive = max_ts + timedelta(microseconds=1)
    midpoint = min_ts + (end_exclusive - min_ts) / 2
    return PeriodResolution(
        current=Period("current", midpoint, end_exclusive),
        previous=Period("previous", min_ts, midpoint),
        grain=Grain.EQUAL_SPAN,
        strategy="equal_span_split",
        anchor=max_ts,
    )


def _bucket_is_complete(
    max_ts: datetime, bucket_end_exclusive: datetime, detected_frequency: str | None
) -> bool:
    """Has the bucket holding the newest row finished being collected?

    Judged against the data's own maximum, never the clock, so the answer is
    reproducible from the file.

    The test has to be relative to how often the data is reported. Daily rows
    ending on the 15th of a month clearly leave that month half-collected. But
    *monthly* rows are typically stamped mid-month, so "the last row is not on
    the 31st" says nothing at all - treating that as partial would throw away
    the newest period and compare the two before it.

    So completeness asks: does one more observation, at this data's own step
    size, land beyond the bucket? If the reporting frequency is unknown, assume
    complete - dropping a real period is a worse error than keeping a partial
    one, which the row counts in the response will reveal anyway.
    """
    grain = FREQUENCY_TO_GRAIN.get((detected_frequency or "").lower())
    if grain is None:
        return True
    return shift_bucket(max_ts, grain, 1) >= bucket_end_exclusive


def resolve_periods(
    *,
    comparison: ComparisonPeriod,
    comparison_config: dict[str, Any] | None,
    min_ts: datetime,
    max_ts: datetime,
    detected_frequency: str | None,
) -> PeriodResolution:
    """Turn a comparison setting into two concrete windows."""
    if comparison is ComparisonPeriod.CUSTOM:
        return _resolve_custom(comparison_config, max_ts)

    grain = resolve_grain(comparison, detected_frequency)
    if grain is Grain.EQUAL_SPAN:
        return _resolve_equal_span(min_ts, max_ts)

    anchor_start = bucket_start(max_ts, grain)
    anchor_end = bucket_end(anchor_start, grain)

    complete = _bucket_is_complete(max_ts, anchor_end, detected_frequency)
    excluded: Period | None = None

    current_start = anchor_start
    if not complete and shift_bucket(anchor_start, grain, -1) >= bucket_start(min_ts, grain):
        excluded = Period("partial", anchor_start, anchor_end)
        current_start = shift_bucket(anchor_start, grain, -1)

    current_end = bucket_end(current_start, grain)
    previous_start = shift_bucket(current_start, grain, -1)

    return PeriodResolution(
        current=Period("current", current_start, current_end),
        previous=Period("previous", previous_start, current_start),
        grain=grain,
        strategy="calendar_bucket",
        anchor=max_ts,
        excluded_partial_period=excluded,
    )
