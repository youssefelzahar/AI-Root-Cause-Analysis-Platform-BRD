"""DuckDB profiling engine (PRD section 9).

Two-stage read:

* **Stage A** reads every column as VARCHAR. Nothing can fail to parse, which
  is what makes type inference and the "convertible values" reporting in PRD
  section 10 trustworthy rather than dependent on a sniffer's guess.
* **Stage B** re-reads with the resolved types and computes the numeric and
  temporal statistics.

All work happens in a handful of bounded-memory scans; the file is never loaded
into Python.
"""

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.analysis.duckdb_session import duckdb_connection, quote_identifier, quote_literal, source_relation
from app.analysis.type_inference import (
    DUCKDB_CAST_TARGET,
    TYPE_PRECEDENCE,
    ColumnInference,
    cast_success_predicate,
    cleaned_numeric_expression,
    null_sentinel_predicate,
    resolve_type,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.enums import InferredType

logger = get_logger(__name__)

# Batched so a 500-column dataset does not build one enormous SELECT.
COLUMN_BATCH = 40


@dataclass
class ColumnStats:
    name: str
    ordinal: int
    inference: ColumnInference
    null_count: int = 0
    null_pct: float = 0.0
    unique_count: int | None = None
    unique_pct: float | None = None
    min_value: str | None = None
    max_value: str | None = None
    mean: float | None = None
    median: float | None = None
    stddev: float | None = None
    percentiles: dict[str, float] = field(default_factory=dict)
    outlier_count: int | None = None
    outlier_lower: float | None = None
    outlier_upper: float | None = None
    top_values: list[dict[str, Any]] = field(default_factory=list)
    datetime_stats: dict[str, Any] = field(default_factory=dict)
    avg_text_length: float | None = None


@dataclass
class ProfileResult:
    row_count: int
    column_count: int
    duplicate_row_count: int | None
    duplicate_row_pct: float | None
    duplicate_check_skipped: bool
    missing_cell_count: int
    missing_cell_pct: float
    exact_quantiles: bool
    columns: list[ColumnStats]


def _clean_float(value: Any) -> float | None:
    """DuckDB can return NaN/Infinity, which are not valid JSON."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:255]


def _batched(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def profile_file(path: Path, file_format: str) -> ProfileResult:
    with duckdb_connection() as conn:
        return _profile(conn, path, file_format)


def _profile(conn, path: Path, file_format: str) -> ProfileResult:  # noqa: ANN001
    varchar_rel = source_relation(path, file_format, all_varchar=True)

    # --- schema ---------------------------------------------------------
    describe = conn.execute(f"DESCRIBE SELECT * FROM {varchar_rel}").fetchall()
    column_names = [row[0] for row in describe]
    if not column_names:
        return ProfileResult(0, 0, None, None, False, 0, 0.0, True, [])

    typed_rel = source_relation(path, file_format, all_varchar=False)
    typed_describe = conn.execute(f"DESCRIBE SELECT * FROM {typed_rel}").fetchall()
    declared_types = {row[0]: row[1] for row in typed_describe}

    row_count = conn.execute(f"SELECT count(*) FROM {varchar_rel}").fetchone()[0]
    column_count = len(column_names)
    if row_count == 0:
        empty = [
            ColumnStats(
                name=name,
                ordinal=index,
                inference=resolve_type(
                    column=name,
                    raw_type=declared_types.get(name, "VARCHAR"),
                    non_null_count=0,
                    null_count=0,
                    raw_castable={},
                ),
            )
            for index, name in enumerate(column_names)
        ]
        return ProfileResult(0, column_count, 0, 0.0, False, 0, 0.0, True, empty)

    # --- pass 1: nulls + TRY_CAST counts (stage A, all VARCHAR) ---------
    null_counts: dict[str, int] = {}
    raw_castable: dict[str, dict[InferredType, int]] = {name: {} for name in column_names}
    cleaned_castable: dict[str, dict[InferredType, int]] = {name: {} for name in column_names}
    leading_zeros: dict[str, bool] = {}
    text_lengths: dict[str, float | None] = {}

    for batch in _batched(column_names, COLUMN_BATCH):
        selects: list[str] = []
        for name in batch:
            col = quote_identifier(name)
            alias = quote_literal(name)
            missing = null_sentinel_predicate(col)
            selects.append(f"count(*) FILTER (WHERE {missing}) AS n_{len(selects)}")
            for target in TYPE_PRECEDENCE:
                predicate = cast_success_predicate(f"trim({col})", target)
                selects.append(
                    f"count(*) FILTER (WHERE NOT {missing} AND {predicate}) AS r_{len(selects)}"
                )
            cleaned = cleaned_numeric_expression(col)
            for target in (InferredType.INTEGER, InferredType.NUMERIC):
                predicate = cast_success_predicate(cleaned, target)
                selects.append(
                    f"count(*) FILTER (WHERE NOT {missing} AND {predicate}) AS c_{len(selects)}"
                )
            # Leading-zero codes must not be converted to numbers.
            selects.append(
                f"count(*) FILTER (WHERE NOT {missing} AND regexp_matches(trim({col}), '^0[0-9]+$')) "
                f"AS z_{len(selects)}"
            )
            selects.append(f"avg(length({col})) AS l_{len(selects)}")
            _ = alias

        rows = conn.execute(f"SELECT {', '.join(selects)} FROM {varchar_rel}").fetchone()

        cursor = 0
        for name in batch:
            null_counts[name] = int(rows[cursor] or 0)
            cursor += 1
            for target in TYPE_PRECEDENCE:
                raw_castable[name][target] = int(rows[cursor] or 0)
                cursor += 1
            for target in (InferredType.INTEGER, InferredType.NUMERIC):
                cleaned_castable[name][target] = int(rows[cursor] or 0)
                cursor += 1
            leading_zero_count = int(rows[cursor] or 0)
            cursor += 1
            avg_len = _clean_float(rows[cursor])
            cursor += 1
            non_null = row_count - null_counts[name]
            leading_zeros[name] = non_null > 0 and leading_zero_count / non_null > 0.10
            text_lengths[name] = avg_len

    # --- distinct counts (needed before boolean inference) --------------
    distinct_counts: dict[str, int] = {}
    for batch in _batched(column_names, COLUMN_BATCH):
        selects = [
            f"count(DISTINCT {quote_identifier(name)}) AS d_{index}" for index, name in enumerate(batch)
        ]
        rows = conn.execute(f"SELECT {', '.join(selects)} FROM {varchar_rel}").fetchone()
        for name, value in zip(batch, rows, strict=True):
            distinct_counts[name] = int(value or 0)

    # --- resolve types ---------------------------------------------------
    inferences: dict[str, ColumnInference] = {}
    for name in column_names:
        non_null = row_count - null_counts[name]
        inferences[name] = resolve_type(
            column=name,
            raw_type=declared_types.get(name, "VARCHAR"),
            non_null_count=non_null,
            null_count=null_counts[name],
            raw_castable=raw_castable[name],
            cleaned_castable=cleaned_castable[name],
            distinct_count=distinct_counts.get(name),
            has_leading_zeros=leading_zeros.get(name, False),
        )

    # --- sample invalid values for the columns that need conversion ------
    for name, inference in inferences.items():
        if inference.invalid_value_count == 0 or inference.inferred_type == InferredType.STRING:
            continue
        col = quote_identifier(name)
        missing = null_sentinel_predicate(col)
        cast_type = DUCKDB_CAST_TARGET.get(inference.inferred_type)
        if not cast_type:
            continue
        expression = cleaned_numeric_expression(col) if inference.used_cleaning else f"trim({col})"
        samples = conn.execute(
            f"SELECT DISTINCT {col} FROM {varchar_rel} "
            f"WHERE NOT {missing} AND TRY_CAST({expression} AS {cast_type}) IS NULL LIMIT 5"
        ).fetchall()
        inference.sample_invalid_values = [str(row[0]) for row in samples if row[0] is not None]

    stats = {
        name: ColumnStats(
            name=name,
            ordinal=index,
            inference=inferences[name],
            null_count=null_counts[name],
            null_pct=round(null_counts[name] / row_count * 100, 4),
            unique_count=distinct_counts.get(name),
            unique_pct=round(distinct_counts.get(name, 0) / row_count * 100, 4),
            avg_text_length=text_lengths.get(name),
        )
        for index, name in enumerate(column_names)
    }

    # --- pass 2: typed statistics ---------------------------------------
    exact_quantiles = row_count <= settings.exact_quantile_row_limit
    numeric_columns = [n for n in column_names if inferences[n].is_numeric]
    temporal_columns = [n for n in column_names if inferences[n].is_temporal]

    if numeric_columns:
        _profile_numeric(conn, varchar_rel, numeric_columns, inferences, stats, exact_quantiles)
    if temporal_columns:
        _profile_temporal(conn, varchar_rel, temporal_columns, inferences, stats)

    categorical_columns = [
        n for n in column_names if not inferences[n].is_numeric and not inferences[n].is_temporal
    ]
    if categorical_columns:
        _profile_categorical(conn, varchar_rel, categorical_columns, stats, row_count)
        for name in categorical_columns:
            col = quote_identifier(name)
            row = conn.execute(
                f"SELECT min({col}), max({col}) FROM {varchar_rel} "
                f"WHERE NOT {null_sentinel_predicate(col)}"
            ).fetchone()
            stats[name].min_value = _as_text(row[0])
            stats[name].max_value = _as_text(row[1])

    # --- duplicates ------------------------------------------------------
    duplicate_count: int | None = None
    duplicate_pct: float | None = None
    skipped = column_count > settings.duplicate_check_max_columns
    if not skipped:
        distinct_rows = conn.execute(
            f"SELECT count(*) FROM (SELECT DISTINCT * FROM {varchar_rel})"
        ).fetchone()[0]
        duplicate_count = max(0, row_count - int(distinct_rows))
        duplicate_pct = round(duplicate_count / row_count * 100, 4)

    missing_cells = sum(null_counts.values())
    total_cells = row_count * column_count
    missing_pct = round(missing_cells / total_cells * 100, 4) if total_cells else 0.0

    return ProfileResult(
        row_count=row_count,
        column_count=column_count,
        duplicate_row_count=duplicate_count,
        duplicate_row_pct=duplicate_pct,
        duplicate_check_skipped=skipped,
        missing_cell_count=missing_cells,
        missing_cell_pct=missing_pct,
        exact_quantiles=exact_quantiles,
        columns=[stats[name] for name in column_names],
    )


def _typed_expression(name: str, inference: ColumnInference) -> str:
    """A cast expression yielding the resolved type from the VARCHAR relation."""
    col = quote_identifier(name)
    cast_type = DUCKDB_CAST_TARGET.get(inference.inferred_type, "VARCHAR")
    source = cleaned_numeric_expression(col) if inference.used_cleaning else f"trim({col})"
    return f"TRY_CAST({source} AS {cast_type})"


def _profile_numeric(conn, relation, columns, inferences, stats, exact) -> None:  # noqa: ANN001
    quantile_fn = "quantile_cont" if exact else "approx_quantile"
    percentiles = settings.profile_percentiles

    for batch in _batched(columns, COLUMN_BATCH // 2 or 1):
        selects: list[str] = []
        for name in batch:
            expr = _typed_expression(name, inferences[name])
            selects.extend(
                [
                    f"min({expr})",
                    f"max({expr})",
                    f"avg({expr})",
                    f"median({expr})",
                    f"stddev_samp({expr})",
                ]
            )
            for percentile in percentiles:
                selects.append(f"{quantile_fn}({expr}, {percentile})")

        rows = conn.execute(f"SELECT {', '.join(selects)} FROM {relation}").fetchone()

        cursor = 0
        for name in batch:
            entry = stats[name]
            entry.min_value = _as_text(_clean_float(rows[cursor]))
            entry.max_value = _as_text(_clean_float(rows[cursor + 1]))
            entry.mean = _clean_float(rows[cursor + 2])
            entry.median = _clean_float(rows[cursor + 3])
            entry.stddev = _clean_float(rows[cursor + 4])
            cursor += 5
            for percentile in percentiles:
                value = _clean_float(rows[cursor])
                if value is not None:
                    entry.percentiles[f"p{int(percentile * 100)}"] = value
                cursor += 1

    # Outliers: IQR 1.5x fences. The PRD does not define "outlier indicators",
    # so the method and the bounds are both reported alongside the count.
    for batch in _batched(columns, COLUMN_BATCH):
        fence_selects: list[str] = []
        eligible: list[str] = []
        for name in batch:
            entry = stats[name]
            p25 = entry.percentiles.get("p25")
            p75 = entry.percentiles.get("p75")
            if p25 is None or p75 is None:
                continue
            iqr = p75 - p25
            lower, upper = p25 - 1.5 * iqr, p75 + 1.5 * iqr
            entry.outlier_lower, entry.outlier_upper = lower, upper
            expr = _typed_expression(name, inferences[name])
            fence_selects.append(
                f"count(*) FILTER (WHERE {expr} IS NOT NULL AND ({expr} < {lower} OR {expr} > {upper}))"
            )
            eligible.append(name)
        if not fence_selects:
            continue
        rows = conn.execute(f"SELECT {', '.join(fence_selects)} FROM {relation}").fetchone()
        for name, value in zip(eligible, rows, strict=True):
            stats[name].outlier_count = int(value or 0)


def _frequency_from_days(days: float | None) -> str | None:
    if days is None:
        return None
    if days <= 0:
        return None
    if days == 1:
        return "daily"
    if days == 7:
        return "weekly"
    if 28 <= days <= 31:
        return "monthly"
    if 89 <= days <= 92:
        return "quarterly"
    if 365 <= days <= 366:
        return "yearly"
    return None


def _profile_temporal(conn, relation, columns, inferences, stats) -> None:  # noqa: ANN001
    for name in columns:
        expr = _typed_expression(name, inferences[name])
        row = conn.execute(
            f"SELECT min({expr}), max({expr}), count(DISTINCT date_trunc('day', {expr})) "
            f"FROM {relation}"
        ).fetchone()
        min_date, max_date, distinct_days = row[0], row[1], int(row[2] or 0)

        entry = stats[name]
        entry.min_value = _as_text(min_date)
        entry.max_value = _as_text(max_date)

        # Detect frequency from the modal gap between consecutive distinct days.
        gaps = conn.execute(
            f"""
            SELECT delta, count(*) AS n FROM (
                SELECT date_diff('day', lag(d) OVER (ORDER BY d), d) AS delta
                FROM (SELECT DISTINCT date_trunc('day', {expr}) AS d FROM {relation}
                      WHERE {expr} IS NOT NULL)
            ) WHERE delta IS NOT NULL
            GROUP BY delta ORDER BY n DESC LIMIT 1
            """
        ).fetchone()

        detected: str | None = None
        confidence: float | None = None
        if gaps:
            modal_delta, modal_count = float(gaps[0]), int(gaps[1])
            total_gaps = max(1, distinct_days - 1)
            confidence = round(modal_count / total_gaps, 4)
            # Below 0.6 the series is too irregular to claim a frequency.
            if confidence >= 0.6:
                detected = _frequency_from_days(modal_delta)

        expected_periods: int | None = None
        missing_periods: int | None = None
        if detected and min_date is not None and max_date is not None:
            span_days = (max_date - min_date).days if hasattr(max_date - min_date, "days") else None
            per_period = {"daily": 1, "weekly": 7, "monthly": 30.44, "quarterly": 91.31, "yearly": 365.25}
            step = per_period.get(detected)
            if span_days is not None and step:
                expected_periods = int(span_days / step) + 1
                missing_periods = max(0, expected_periods - distinct_days)

        entry.datetime_stats = {
            "min_date": str(min_date) if min_date is not None else None,
            "max_date": str(max_date) if max_date is not None else None,
            "distinct_periods": distinct_days,
            "detected_frequency": detected,
            "frequency_confidence": confidence,
            "expected_periods": expected_periods,
            # Null rather than a fabricated number when frequency is unknown.
            "missing_periods": missing_periods,
        }


def _profile_categorical(conn, relation, columns, stats, row_count: int) -> None:  # noqa: ANN001
    """Top-K values for every categorical column in one scan.

    A UNION ALL of (column, value) pairs avoids one table scan per column,
    which matters when a dataset has hundreds of categorical fields.
    """
    top_k = settings.profile_top_k
    for batch in _batched(columns, COLUMN_BATCH):
        unions = [
            f"SELECT {quote_literal(name)} AS col, CAST({quote_identifier(name)} AS VARCHAR) AS val "
            f"FROM {relation}"
            for name in batch
        ]
        sql = f"""
            SELECT col, val, cnt FROM (
                SELECT col, val, count(*) AS cnt,
                       row_number() OVER (PARTITION BY col ORDER BY count(*) DESC, val) AS rn
                FROM ({" UNION ALL ".join(unions)})
                WHERE val IS NOT NULL
                GROUP BY col, val
            ) WHERE rn <= {top_k}
            ORDER BY col, cnt DESC
        """
        for col_name, value, count in conn.execute(sql).fetchall():
            entry = stats.get(col_name)
            if entry is None:
                continue
            entry.top_values.append(
                {
                    "value": value,
                    "count": int(count),
                    "pct": round(int(count) / row_count * 100, 4) if row_count else 0.0,
                }
            )
