"""SQL builders for period totals and dimension breakdowns.

Every function returns a SQL string plus its bind parameters. Nothing here holds
a connection or executes - that is ``engine.py``'s job alone.

The shape of these queries is what keeps a full investigation at roughly eight
statements and a single scan of the source file: the data is projected once into
a temp table, both periods are aggregated in one pass with FILTER, and every
dimension is broken down in one statement via a UNION ALL unpivot - the same
trick ``profiler._profile_categorical`` already uses for top-K values.
"""

from datetime import datetime

from app.analysis.duckdb_session import quote_identifier, quote_literal
from app.analysis.rca.casting import aggregate_expression
from app.analysis.rca.models import Period
from app.db.models.enums import Aggregation

BASE_TABLE = "rca_base"

FILTER_OPERATORS: dict[str, str] = {
    "eq": "=",
    "ne": "<>",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}


def dimension_alias(index: int) -> str:
    return f"d{index}"


def build_base_table_sql(
    relation: str,
    *,
    time_expr: str,
    measure_expr: str,
    dimension_exprs: list[str],
    where_clause: str = "",
) -> str:
    """Project the source into a narrow temp table.

    Only ``2 + len(dimensions)`` columns survive, so everything downstream is an
    in-memory aggregate over a fraction of the original width. DuckDB spills to
    the configured temp directory under the configured memory limit, so this
    stays bounded on a 200 MB source.
    """
    columns = [f"{time_expr} AS ts", f"{measure_expr} AS m"]
    columns += [f"{expr} AS {dimension_alias(i)}" for i, expr in enumerate(dimension_exprs)]
    body = ",\n       ".join(columns)
    suffix = f"\nWHERE {where_clause}" if where_clause else ""
    return f"CREATE TEMP TABLE {BASE_TABLE} AS\nSELECT {body}\nFROM {relation}{suffix}"


def _timestamp_literal(moment: datetime) -> str:
    # Built from a datetime object by Python, never from user text - the
    # comparison_config path parses to datetime before reaching any SQL string.
    return f"TIMESTAMP '{moment.strftime('%Y-%m-%d %H:%M:%S.%f')}'"


def period_case_expression(current: Period, previous: Period) -> str:
    """Tag each row with the window it belongs to.

    Half-open on both sides, so a row landing exactly on a boundary cannot be
    counted in both periods.
    """
    return (
        "CASE "
        f"WHEN ts >= {_timestamp_literal(current.start)} AND ts < {_timestamp_literal(current.end)} "
        "THEN 'current' "
        f"WHEN ts >= {_timestamp_literal(previous.start)} AND ts < {_timestamp_literal(previous.end)} "
        "THEN 'previous' "
        "END"
    )


def build_bounds_sql() -> str:
    """Min/max timestamp and parse counts, used to resolve the periods."""
    return (
        "SELECT min(ts) AS min_ts, max(ts) AS max_ts, "
        "count(*) AS total_rows, count(ts) AS parsed_time_rows, "
        f"count(m) AS parsed_measure_rows FROM {BASE_TABLE}"
    )


def build_totals_sql(aggregation: Aggregation, current: Period, previous: Period) -> str:
    """Both periods' totals in one pass over the temp table."""
    agg = aggregate_expression(aggregation, "m")
    tagged = f"SELECT *, {period_case_expression(current, previous)} AS period FROM {BASE_TABLE}"
    return f"""
WITH tagged AS ({tagged})
SELECT
    {agg} FILTER (WHERE period = 'current')  AS current_value,
    {agg} FILTER (WHERE period = 'previous') AS previous_value,
    count(m) FILTER (WHERE period = 'current')  AS current_count,
    count(m) FILTER (WHERE period = 'previous') AS previous_count,
    count(*) FILTER (WHERE period = 'current')  AS current_rows,
    count(*) FILTER (WHERE period = 'previous') AS previous_rows,
    count(*) FILTER (WHERE period IS NULL)      AS rows_outside_periods
FROM tagged
""".strip()


def build_breakdown_sql(
    aggregation: Aggregation,
    dimensions: list[str],
    current: Period,
    previous: Period,
    *,
    limit: int,
    parent_predicates: list[str] | None = None,
    dimension_offsets: dict[str, int] | None = None,
) -> str:
    """Break every dimension down in a single statement.

    Grouping period-tagged rows gives FULL OUTER semantics for free: one row per
    segment present in *either* period, with NULL on the missing side. A join
    would be worse - LEFT JOIN silently drops segments that disappeared, INNER
    JOIN drops both new and lost ones, and those are exactly the segments most
    worth reporting.

    NULL (segment absent) stays distinguishable from 0 (present, measured zero),
    so new/lost detection reads row counts rather than a falsy value.
    """
    offsets = dimension_offsets or {name: i for i, name in enumerate(dimensions)}
    agg = aggregate_expression(aggregation, "m")

    where = ""
    if parent_predicates:
        where = "\n    WHERE " + " AND ".join(parent_predicates)

    tagged = (
        f"SELECT *, {period_case_expression(current, previous)} AS period "
        f"FROM {BASE_TABLE}{where}"
    )

    branches = [
        f"    SELECT {quote_literal(name)} AS dim, "
        f"{dimension_alias(offsets[name])} AS seg, m, period "
        "FROM tagged WHERE period IS NOT NULL"
        for name in dimensions
    ]
    unpivoted = "\n    UNION ALL\n".join(branches)

    return f"""
WITH tagged AS ({tagged}),
unpivoted AS (
{unpivoted}
),
grouped AS (
    SELECT dim, seg,
           {agg} FILTER (WHERE period = 'current')  AS current_value,
           {agg} FILTER (WHERE period = 'previous') AS previous_value,
           count(m) FILTER (WHERE period = 'current')  AS current_count,
           count(m) FILTER (WHERE period = 'previous') AS previous_count,
           count(*) FILTER (WHERE period = 'current')  AS current_rows,
           count(*) FILTER (WHERE period = 'previous') AS previous_rows
    FROM unpivoted
    GROUP BY dim, seg
)
SELECT * FROM grouped
QUALIFY row_number() OVER (
    PARTITION BY dim
    ORDER BY abs(coalesce(current_value, 0) - coalesce(previous_value, 0)) DESC, seg
) <= {int(limit)}
""".strip()


def build_segment_count_sql(dimensions: list[str], dimension_offsets: dict[str, int]) -> str:
    """True cardinality per dimension, used only when truncation fires."""
    branches = [
        f"    SELECT {quote_literal(name)} AS dim, "
        f"{dimension_alias(dimension_offsets[name])} AS seg FROM {BASE_TABLE}"
        for name in dimensions
    ]
    unpivoted = "\n    UNION ALL\n".join(branches)
    return f"""
WITH unpivoted AS (
{unpivoted}
)
SELECT dim, count(DISTINCT seg) AS segment_count FROM unpivoted GROUP BY dim
""".strip()


def build_distinct_overlap_sql(
    dimension: str,
    offset: int,
    current: Period,
    previous: Period,
) -> str:
    """Detect whether COUNT_DISTINCT is genuinely additive over this dimension.

    ``sum(per-group distinct) - overall distinct`` is zero exactly when the
    dimension partitions the key set. Checking is cheap and turns a blanket
    "not attributable" into a real decomposition whenever it is valid.
    """
    alias = dimension_alias(offset)
    tagged = f"SELECT *, {period_case_expression(current, previous)} AS period FROM {BASE_TABLE}"
    return f"""
WITH tagged AS ({tagged}),
per_group AS (
    SELECT period, {alias} AS seg, count(DISTINCT m) AS d
    FROM tagged WHERE period IS NOT NULL GROUP BY period, {alias}
),
overall AS (
    SELECT period, count(DISTINCT m) AS d
    FROM tagged WHERE period IS NOT NULL GROUP BY period
)
SELECT o.period, coalesce(sum(g.d), 0) - o.d AS overlap_gap
FROM overall o LEFT JOIN per_group g ON g.period = o.period
GROUP BY o.period, o.d
""".strip()


def build_filter_clause(
    filters: tuple[dict, ...], available: dict[str, str]
) -> tuple[str, list]:
    """Translate the KPI definition's free-form filters into a safe predicate.

    ``kpi_definitions.filters`` has no schema and no consumer today. A closed
    grammar is defined here: the column must exist in the relation, the operator
    must be one of a fixed set, and every value binds as a parameter. There is
    deliberately no passthrough for raw SQL.
    """
    from app.core.exceptions import ValidationError

    clauses: list[str] = []
    params: list = []

    for entry in filters:
        if not isinstance(entry, dict):
            raise ValidationError("Each KPI filter must be an object.", code="KPI_FILTER_INVALID")
        column = entry.get("column")
        op = str(entry.get("op", "eq")).lower()
        value = entry.get("value")

        if not column or column not in available:
            raise ValidationError(
                f"The filter column {column!r} is not present in this dataset.",
                code="KPI_FILTER_INVALID",
                details={"column": column},
            )
        quoted = quote_identifier(column)

        if op in FILTER_OPERATORS:
            clauses.append(f"{quoted} {FILTER_OPERATORS[op]} ?")
            params.append(value)
        elif op in {"in", "not_in"}:
            values = value if isinstance(value, list) else [value]
            if not values:
                raise ValidationError(
                    "An 'in' filter needs at least one value.", code="KPI_FILTER_INVALID"
                )
            placeholders = ", ".join("?" for _ in values)
            keyword = "IN" if op == "in" else "NOT IN"
            clauses.append(f"{quoted} {keyword} ({placeholders})")
            params.extend(values)
        elif op == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise ValidationError(
                    "A 'between' filter needs exactly two values.", code="KPI_FILTER_INVALID"
                )
            clauses.append(f"{quoted} BETWEEN ? AND ?")
            params.extend(value)
        elif op == "is_null":
            clauses.append(f"{quoted} IS NULL")
        elif op == "is_not_null":
            clauses.append(f"{quoted} IS NOT NULL")
        else:
            raise ValidationError(
                f"Unsupported filter operator {op!r}.",
                code="KPI_FILTER_INVALID",
                details={"op": op},
            )

    return " AND ".join(clauses), params
