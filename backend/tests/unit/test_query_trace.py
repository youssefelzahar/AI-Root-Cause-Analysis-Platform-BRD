"""The query trace: what ran, why, and what it must never record.

Driven against real DuckDB relations like ``test_rca_sql``, because the point of
the trace is that the SQL in it is the SQL that executed - which can only be
observed by executing it.
"""

import duckdb
import pytest

from app.analysis.duckdb_session import duckdb_connection, source_relation
from app.analysis.rca import run_investigation
from app.analysis.rca.models import RcaSpec
from app.analysis.trace import Probe, Purpose, QueryStatus, QueryTracer
from app.db.models.enums import Aggregation, ComparisonPeriod

GOLDEN = """order_date,region,product,segment,revenue
2026-06-15,Cairo,A,Enterprise,500
2026-06-15,Cairo,B,SMB,300
2026-06-15,Giza,A,Enterprise,400
2026-06-15,Giza,B,SMB,300
2026-07-15,Cairo,A,Enterprise,200
2026-07-15,Cairo,B,SMB,300
2026-07-15,Giza,A,Enterprise,400
2026-07-15,Giza,B,SMB,300
"""

# A filter value distinctive enough that finding it anywhere in the trace is
# unambiguous rather than a coincidence.
SECRET_SEGMENT = "Enterprise"


def _write(tmp_path, content: str = GOLDEN, name: str = "data.csv"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _spec(**overrides) -> RcaSpec:
    defaults = dict(
        kpi_name="Revenue",
        measure_column="revenue",
        aggregation=Aggregation.SUM,
        time_column="order_date",
        dimensions=("region", "product", "segment"),
        comparison=ComparisonPeriod.PREVIOUS_MONTH,
        detected_frequency="monthly",
    )
    defaults.update(overrides)
    return RcaSpec(**defaults)


def _run(path, spec: RcaSpec) -> tuple:
    probe = Probe()
    with duckdb_connection() as conn:
        result = run_investigation(conn, source_relation(path, "csv"), spec, probe=probe)
    return result, probe


# --- the tracer on its own ----------------------------------------------------


def test_a_traced_statement_records_its_purpose_and_row_count():
    tracer = QueryTracer()
    with duckdb_connection() as conn:
        rows = tracer.execute(
            conn, "SELECT 1 UNION ALL SELECT 2", purpose=Purpose.KPI_PERIOD_TOTALS
        ).fetchall()

    assert len(rows) == 2
    assert tracer.count == 1
    record = tracer.records[0]
    assert record.sequence == 1
    assert record.purpose is Purpose.KPI_PERIOD_TOTALS
    assert record.sql == "SELECT 1 UNION ALL SELECT 2"
    assert record.status is QueryStatus.OK
    assert record.rows_returned == 2
    assert record.duration_ms >= 0
    assert record.error is None


def test_rows_returned_stays_none_when_nothing_is_fetched():
    """A CREATE TABLE returns no rows, and reporting zero would be a claim."""
    tracer = QueryTracer()
    with duckdb_connection() as conn:
        tracer.execute(
            conn, "CREATE TEMP TABLE t AS SELECT 1 AS a", purpose=Purpose.PROJECT_BASE_TABLE
        )

    assert tracer.records[0].rows_returned is None


def test_fetchone_counts_a_missing_row_as_zero():
    tracer = QueryTracer()
    with duckdb_connection() as conn:
        tracer.execute(conn, "SELECT 1 WHERE false", purpose=Purpose.SERIES_BOUNDS).fetchone()
        tracer.execute(conn, "SELECT 1", purpose=Purpose.SERIES_BOUNDS).fetchone()

    assert [r.rows_returned for r in tracer.records] == [0, 1]


def test_a_failing_statement_is_recorded_and_still_raises():
    """A trace that silently drops the statement that broke is worse than none."""
    tracer = QueryTracer()
    with duckdb_connection() as conn, pytest.raises(duckdb.Error):
        tracer.execute(conn, "SELECT * FROM no_such_table", purpose=Purpose.DIMENSION_BREAKDOWN)

    assert tracer.count == 1
    record = tracer.records[0]
    assert record.status is QueryStatus.FAILED
    assert record.error
    assert record.rows_returned is None


def test_the_tracer_records_how_many_parameters_were_bound_but_not_their_values():
    tracer = QueryTracer()
    with duckdb_connection() as conn:
        tracer.execute(
            conn,
            "SELECT ? AS a, ? AS b",
            ["alice@example.com", "s3cret"],
            purpose=Purpose.DRILLDOWN_BREAKDOWN,
        ).fetchall()

    record = tracer.records[0]
    assert record.parameter_count == 2
    assert not hasattr(record, "params")
    assert not hasattr(record, "parameters")
    blob = repr(record)
    assert "alice@example.com" not in blob
    assert "s3cret" not in blob


def test_find_returns_the_statement_that_expanded_a_given_node():
    tracer = QueryTracer()
    with duckdb_connection() as conn:
        tracer.execute(conn, "SELECT 1", purpose=Purpose.DIMENSION_BREAKDOWN).fetchall()
        tracer.execute(
            conn, "SELECT 2", purpose=Purpose.DRILLDOWN_BREAKDOWN, node_id="region=Cairo"
        ).fetchall()
        tracer.execute(
            conn, "SELECT 3", purpose=Purpose.DRILLDOWN_BREAKDOWN, node_id="region=Giza"
        ).fetchall()

    assert tracer.find(Purpose.DRILLDOWN_BREAKDOWN, node_id="region=Cairo").sql == "SELECT 2"
    assert tracer.find(Purpose.DRILLDOWN_BREAKDOWN, node_id="region=Giza").sql == "SELECT 3"
    assert tracer.find(Purpose.DIMENSION_BREAKDOWN).sql == "SELECT 1"
    assert tracer.find(Purpose.SERIES_AGGREGATE) is None


# --- the tracer inside the engine ---------------------------------------------


def test_the_trace_and_the_reported_statement_count_cannot_disagree(tmp_path):
    result, probe = _run(_write(tmp_path), _spec())

    assert probe.queries.count == result.evidence.statements_executed


def test_the_golden_investigation_traces_seven_statements(tmp_path):
    """Seven is the figure the spec quotes, and it should stay observable."""
    _, probe = _run(_write(tmp_path), _spec())

    assert probe.queries.count == 7
    assert [r.purpose for r in probe.queries.records] == [
        Purpose.DESCRIBE_RELATION,
        Purpose.PROJECT_BASE_TABLE,
        Purpose.RESOLVE_PERIOD_BOUNDS,
        Purpose.KPI_PERIOD_TOTALS,
        Purpose.DIMENSION_BREAKDOWN,
        Purpose.DRILLDOWN_BREAKDOWN,
        Purpose.DRILLDOWN_BREAKDOWN,
    ]


def test_every_traced_statement_succeeded_and_carries_real_sql(tmp_path):
    _, probe = _run(_write(tmp_path), _spec())

    for record in probe.queries.records:
        assert record.status is QueryStatus.OK
        assert record.sql.strip()
        assert record.error is None
        # Not a label, a description or a placeholder: a statement.
        assert record.sql.lstrip().upper().startswith(("SELECT", "WITH", "CREATE", "DESCRIBE"))


def test_the_drilldown_statements_name_the_node_they_expanded(tmp_path):
    _, probe = _run(_write(tmp_path), _spec())

    drills = [r for r in probe.queries.records if r.purpose is Purpose.DRILLDOWN_BREAKDOWN]
    assert drills
    for record in drills:
        assert record.node_id
        assert record.depth is not None and record.depth >= 2


def test_time_inside_duckdb_never_exceeds_the_reported_wall_clock(tmp_path):
    result, probe = _run(_write(tmp_path), _spec())

    assert probe.queries.duration_ms <= result.evidence.duration_ms


def test_no_statement_is_executed_twice(tmp_path):
    """Catches two nodes at one depth resolving to the same predicate.

    The statement-count test would not: the count would be right and the work
    would still be duplicated.
    """
    _, probe = _run(_write(tmp_path), _spec())

    executed = [(r.sql, r.parameter_count) for r in probe.queries.records]
    assert len(set(executed)) == len(executed)


def test_a_filtered_investigation_keeps_the_filter_value_out_of_the_trace(tmp_path):
    """The SQL is stored verbatim, so it must not be where values live.

    Filters bind their values, which is what makes storing the text safe. If a
    builder ever interpolated one instead, this fails.
    """
    spec = _spec(filters=({"column": "segment", "op": "eq", "value": SECRET_SEGMENT},))
    _, probe = _run(_write(tmp_path), spec)

    assert probe.queries.count > 0
    bound = 0
    for record in probe.queries.records:
        assert SECRET_SEGMENT not in record.sql
        assert SECRET_SEGMENT not in repr(record)
        bound += record.parameter_count
    # The value did reach DuckDB - as a parameter, which is the whole point.
    assert bound > 0


def test_an_investigation_runs_untraced_when_no_probe_is_passed(tmp_path):
    """The probe is optional, so every existing caller keeps working."""
    with duckdb_connection() as conn:
        result = run_investigation(conn, source_relation(_write(tmp_path), "csv"), _spec())

    assert result.evidence.statements_executed == 7
