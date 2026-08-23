"""What was actually executed, and why - so "7 queries in 141 ms" is inspectable.

Replaces the two identical private ``_Counter`` classes that used to live in
``app.analysis.rca.engine`` and ``app.analysis.anomaly.engine`` and threw the SQL
away. ``count`` is kept under its old name so every existing read site works
unchanged.

Never fabricate SQL: ``QueryRecord.sql`` is the exact string handed to DuckDB.
And never store the bound parameters. Filters and drill predicates bind their
values (``dimension_analysis.build_filter_clause``, ``engine._drill``), so a
parameter can be a customer name; ``sqlalchemy.engine`` is pinned to WARNING in
``core.logging`` for exactly that reason. Only the count is recorded.

This module is a peer of ``duckdb_session``, not part of either engine package:
both engines emit into it and neither may import the other.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

import duckdb

# A failed statement's message is recorded so a FAILED trace is diagnosable, but
# a DuckDB error can quote the offending literal back, so it is truncated rather
# than stored whole.
MAX_ERROR_CHARS = 500


class QueryStatus(str, Enum):
    OK = "ok"
    FAILED = "failed"


class Purpose(str, Enum):
    """Why a statement ran. One fixed vocabulary across both engines.

    Stored on every traced statement so the query list reads as an explanation
    rather than as seven anonymous SELECTs.
    """

    # --- shared
    DESCRIBE_RELATION = "describe_relation"
    # --- root cause analysis
    PROJECT_BASE_TABLE = "project_base_table"
    RESOLVE_PERIOD_BOUNDS = "resolve_period_bounds"
    KPI_PERIOD_TOTALS = "kpi_period_totals"
    DIMENSION_BREAKDOWN = "dimension_breakdown"
    DISTINCT_OVERLAP_CHECK = "distinct_overlap_check"
    DRILLDOWN_BREAKDOWN = "drilldown_breakdown"
    # --- anomaly detection
    SERIES_BASE_TABLE = "series_base_table"
    SERIES_BOUNDS = "series_bounds"
    SERIES_AGGREGATE = "series_aggregate"


@dataclass(frozen=True)
class QueryRecord:
    """One executed statement.

    There is deliberately no ``params`` field: see the module docstring.
    """

    sequence: int  # 1-based execution order; the trace's identity
    purpose: Purpose
    sql: str  # verbatim, exactly as handed to DuckDB
    parameter_count: int  # how many values were bound, never which
    duration_ms: int
    status: QueryStatus
    rows_returned: int | None = None  # back-filled on fetch; None for DDL
    error: str | None = None
    depth: int | None = None  # drill level, for the drill-down statements
    node_id: str | None = None  # which node this level expanded


class DecisionKind(str, Enum):
    """The kinds of judgement worth recording a reason for."""

    PERIOD_RESOLVED = "period_resolved"
    BASIS_SELECTED = "basis_selected"
    DIMENSION_SELECTED = "dimension_selected"
    SEGMENT_SELECTED = "segment_selected"
    DRILLDOWN_STOPPED = "drilldown_stopped"
    PATTERN_CLASSIFIED = "pattern_classified"
    DRIVER_SUPPRESSED = "driver_suppressed"


@dataclass(frozen=True)
class DecisionRecord:
    """Why the engine selected, stopped or suppressed something.

    ``why`` is a sentence for a reader; ``inputs`` carries the numbers it was
    decided on so the sentence is checkable rather than merely plausible.
    """

    kind: DecisionKind
    subject: str
    outcome: str  # selected | stopped | suppressed | resolved | classified
    reason_code: str
    why: str
    sequence: int = 0  # stamped by Probe.record
    dimension: str | None = None
    depth: int = 0
    inputs: dict[str, Any] = field(default_factory=dict)
    node_id: str | None = None
    evidence_ids: tuple[str, ...] = ()  # stamped later by investigation.graph


class QueryTracer:
    """Records every statement an engine executes.

    Tracing is unconditional: two ``perf_counter`` calls and one frozen
    dataclass per statement, against roughly eight statements per investigation.
    There is no performance case for making it opt-in, and an opt-in trace is
    the one that is off when you need it.
    """

    def __init__(self) -> None:
        self.records: list[QueryRecord] = []

    @property
    def count(self) -> int:
        """Statements executed. The old ``_Counter.count`` contract."""
        return len(self.records)

    @property
    def duration_ms(self) -> int:
        """Time spent inside DuckDB, which is less than the wall clock."""
        return sum(record.duration_ms for record in self.records)

    def execute(
        self,
        conn: duckdb.DuckDBPyConnection,
        sql: str,
        params: Sequence[Any] | None = None,
        *,
        purpose: Purpose = Purpose.DESCRIBE_RELATION,
        depth: int | None = None,
        node_id: str | None = None,
    ) -> "TracedCursor":
        sequence = len(self.records) + 1
        started = time.perf_counter()
        try:
            cursor = conn.execute(sql, params) if params else conn.execute(sql)
        except Exception as exc:
            self.records.append(
                QueryRecord(
                    sequence=sequence,
                    purpose=purpose,
                    sql=sql,
                    parameter_count=len(params or ()),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    status=QueryStatus.FAILED,
                    error=str(exc)[:MAX_ERROR_CHARS],
                    depth=depth,
                    node_id=node_id,
                )
            )
            raise
        self.records.append(
            QueryRecord(
                sequence=sequence,
                purpose=purpose,
                sql=sql,
                parameter_count=len(params or ()),
                duration_ms=int((time.perf_counter() - started) * 1000),
                status=QueryStatus.OK,
                depth=depth,
                node_id=node_id,
            )
        )
        return TracedCursor(cursor, self, sequence)

    def record_rows(self, sequence: int, rows: int) -> None:
        index = sequence - 1
        self.records[index] = replace(self.records[index], rows_returned=rows)

    def find(self, purpose: Purpose, *, node_id: str | None = None) -> QueryRecord | None:
        """The statement that produced a number, for evidence provenance.

        Searches backwards: a drill level runs one statement per node, and the
        caller wants the one that expanded *this* node.
        """
        for record in reversed(self.records):
            if record.purpose is not purpose:
                continue
            if node_id is not None and record.node_id != node_id:
                continue
            return record
        return None


class TracedCursor:
    """Forwards fetches and back-fills ``rows_returned``.

    A proxy rather than a ``fetch=`` argument on ``execute``, so every call site
    stays a one-keyword diff and a caller that never fetches still gets a record.
    """

    def __init__(self, cursor: Any, tracer: QueryTracer, sequence: int) -> None:
        self._cursor = cursor
        self._tracer = tracer
        self._sequence = sequence

    def fetchall(self) -> list[tuple]:
        rows = self._cursor.fetchall()
        self._tracer.record_rows(self._sequence, len(rows))
        return rows

    def fetchone(self) -> tuple | None:
        row = self._cursor.fetchone()
        self._tracer.record_rows(self._sequence, 0 if row is None else 1)
        return row

    def fetchmany(self, size: int = 1) -> list[tuple]:
        rows = self._cursor.fetchmany(size)
        self._tracer.record_rows(self._sequence, len(rows))
        return rows

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


@dataclass
class Probe:
    """One optional out-parameter carrying everything an observer wants.

    Two separate keyword arguments - a tracer and a decision list - would be two
    signature changes on both engines, and a third the next time something needs
    observing. One object is one.
    """

    queries: QueryTracer = field(default_factory=QueryTracer)
    decisions: list[DecisionRecord] = field(default_factory=list)

    def record(self, decision: DecisionRecord) -> None:
        self.decisions.append(replace(decision, sequence=len(self.decisions) + 1))
