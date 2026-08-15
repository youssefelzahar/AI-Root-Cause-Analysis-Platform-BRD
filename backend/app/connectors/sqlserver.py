"""SQL Server connectivity (PRD section 8).

Uses ``pymssql``, which ships manylinux wheels and so needs no system ODBC
driver in the slim Python image. The driver is imported lazily and everything
is behind this module's narrow interface, so swapping to pyodbc later is a
single-file change.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.exceptions import AppError, UpstreamError, UpstreamTimeoutError
from app.core.logging import get_logger, redact

logger = get_logger(__name__)

FETCH_BATCH = 1_000


class DriverUnavailableError(AppError):
    code = "SQLSERVER_DRIVER_UNAVAILABLE"
    status_code = 500


@dataclass
class ConnectionParams:
    host: str
    port: int
    database: str
    username: str
    password: str
    encrypt: bool = True
    trust_server_certificate: bool = False
    login_timeout: int = 10


@dataclass
class QueryResult:
    columns: list[dict[str, Any]] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    elapsed_ms: int = 0


def _driver():  # noqa: ANN202
    try:
        import pymssql
    except ImportError as exc:
        raise DriverUnavailableError(
            "The SQL Server driver is not installed in this environment."
        ) from exc
    return pymssql


def sanitize_db_error(exc: Exception) -> str:
    """Strip anything credential-shaped out of a driver error message.

    Driver errors routinely embed the full connection string.
    """
    return redact(str(exc))[:500]


@contextmanager
def connect(params: ConnectionParams) -> Iterator[Any]:
    pymssql = _driver()
    try:
        connection = pymssql.connect(
            server=params.host,
            port=str(params.port),
            user=params.username,
            password=params.password,
            database=params.database,
            login_timeout=params.login_timeout,
            timeout=settings.sql_max_timeout_seconds,
            charset=settings.sqlserver_charset,
            # Never autocommit: every statement runs in a transaction that is
            # rolled back, so even a guard bypass cannot persist a change.
            autocommit=False,
            appname="RCA-Platform",
        )
    except Exception as exc:
        message = sanitize_db_error(exc)
        lowered = message.lower()
        if "login failed" in lowered or "authentication" in lowered:
            raise UpstreamError(f"Authentication failed: {message}", code="AUTH_FAILED") from exc
        if "timed out" in lowered or "timeout" in lowered:
            raise UpstreamTimeoutError(f"Connection timed out: {message}", code="CONNECT_TIMEOUT") from exc
        raise UpstreamError(f"Could not connect to SQL Server: {message}", code="CONNECT_FAILED") from exc

    try:
        yield connection
    finally:
        try:
            connection.rollback()
        except Exception:
            pass
        try:
            connection.close()
        except Exception:
            pass


def test_connection(params: ConnectionParams) -> dict[str, Any]:
    """Probe a connection.

    Returns a result object rather than raising: a server being unreachable is
    a normal, renderable outcome, not an API error.
    """
    started = time.perf_counter()
    try:
        with connect(params) as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT @@VERSION, DB_NAME()")
            row = cursor.fetchone()
            cursor.close()
        return {
            "ok": True,
            "server_version": (str(row[0]).splitlines()[0] if row and row[0] else None),
            "database": row[1] if row and len(row) > 1 else params.database,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    except AppError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}
    except Exception as exc:
        return {"ok": False, "error_code": "CONNECT_FAILED", "message": sanitize_db_error(exc)}


def run_query(
    params: ConnectionParams,
    sql: str,
    *,
    row_limit: int,
    timeout_seconds: int,
) -> QueryResult:
    """Execute a read-only query with a hard row cap.

    The cap is applied with ``fetchmany`` rather than by wrapping the query in
    ``SELECT TOP (n) ...``: wrapping produces invalid T-SQL whenever the user's
    query has its own ORDER BY, because a derived table may not be ordered.
    """
    started = time.perf_counter()
    with connect(params) as connection:
        cursor = connection.cursor()
        try:
            cursor.execute(f"SET LOCK_TIMEOUT {timeout_seconds * 1000}")
            cursor.execute(sql)

            description = cursor.description or []
            columns = [
                {"name": column[0], "sql_type_code": column[1]} for column in description
            ]

            rows: list[list[Any]] = []
            truncated = False
            while len(rows) <= row_limit:
                batch = cursor.fetchmany(min(FETCH_BATCH, row_limit + 1 - len(rows)))
                if not batch:
                    break
                rows.extend(list(row) for row in batch)
                if len(rows) > row_limit:
                    truncated = True
                    rows = rows[:row_limit]
                    break

            return QueryResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                truncated=truncated,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            message = sanitize_db_error(exc)
            if "timeout" in message.lower():
                raise UpstreamTimeoutError(f"The query timed out: {message}", code="QUERY_TIMEOUT") from exc
            raise UpstreamError(f"The query failed: {message}", code="QUERY_FAILED") from exc
        finally:
            try:
                cursor.close()
            except Exception:
                pass
            # Always roll back, even for a pure SELECT.
            connection.rollback()


def iter_rows(
    params: ConnectionParams, sql: str, *, max_rows: int, timeout_seconds: int
) -> Iterator[tuple[list[str], list[list[Any]]]]:
    """Yield (columns, batch) pairs so a large result can stream to Parquet."""
    with connect(params) as connection:
        cursor = connection.cursor()
        try:
            cursor.execute(f"SET LOCK_TIMEOUT {timeout_seconds * 1000}")
            cursor.execute(sql)
            columns = [column[0] for column in (cursor.description or [])]
            emitted = 0
            while emitted < max_rows:
                batch = cursor.fetchmany(min(FETCH_BATCH, max_rows - emitted))
                if not batch:
                    break
                emitted += len(batch)
                yield columns, [list(row) for row in batch]
        except Exception as exc:
            raise UpstreamError(f"The query failed: {sanitize_db_error(exc)}", code="QUERY_FAILED") from exc
        finally:
            try:
                cursor.close()
            except Exception:
                pass
            connection.rollback()


def list_schema(params: ConnectionParams, *, limit: int = 500) -> dict[str, Any]:
    """Browse tables and columns via INFORMATION_SCHEMA."""
    with connect(params) as connection:
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE
                FROM INFORMATION_SCHEMA.COLUMNS
                ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
                """
            )
            schemas: dict[str, dict[str, list[dict[str, Any]]]] = {}
            for index, (schema, table, column, data_type, nullable) in enumerate(cursor.fetchall()):
                if index >= limit * 50:
                    break
                schemas.setdefault(schema, {}).setdefault(table, []).append(
                    {"name": column, "data_type": data_type, "is_nullable": nullable == "YES"}
                )
            return {
                "schemas": [
                    {
                        "name": schema,
                        "tables": [
                            {"name": table, "columns": columns} for table, columns in tables.items()
                        ],
                    }
                    for schema, tables in schemas.items()
                ]
            }
        except Exception as exc:
            raise UpstreamError(
                f"Could not read the schema: {sanitize_db_error(exc)}", code="SCHEMA_FAILED"
            ) from exc
        finally:
            try:
                cursor.close()
            except Exception:
                pass
            connection.rollback()
