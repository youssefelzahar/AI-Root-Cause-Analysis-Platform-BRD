"""SQL Server connectivity (PRD section 8).

Two drivers, chosen by authentication mode, and only ``connect`` knows which:

* **SQL authentication** uses ``pymssql``, which ships manylinux wheels and so
  needs no system ODBC driver in the slim Python image. This is the path every
  existing connection takes and the only one that works from the container.
* **Windows authentication** uses ``pyodbc`` with ``Trusted_Connection=yes``.
  ``pymssql`` cannot do it at all - it is FreeTDS-based and rejects a
  ``trusted_connection`` argument outright - so there is no way to offer the mode
  without a second driver.

Keeping both rather than moving everything to ``pyodbc`` is deliberate. A wholesale
swap would change the driver under every working connection and would break the
Linux image, which has no unixODBC. This way the new mode is additive: the old path
is byte-for-byte what it was.

Everything past ``connect`` is plain DBAPI 2.0 - ``cursor``, ``execute``,
``description``, ``fetchmany``, ``rollback`` - so the query, streaming and schema
functions are driver-agnostic and untouched by the split. The one place the drivers
disagree is ``cursor.description[1]``: ``pymssql`` reports an integer type code and
``pyodbc`` reports a Python type object. ``_type_code`` normalises that.

**Windows authentication only works when this backend runs on Windows as the user
who holds the SQL Server grant.** It borrows the process's identity, so there is
nothing to borrow inside a Linux container - the mode is for local and on-premise
Windows deployments, and it fails with a typed error anywhere else.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.exceptions import AppError, UpstreamError, UpstreamTimeoutError
from app.core.logging import get_logger, redact
from app.db.models.enums import SqlAuthMode

logger = get_logger(__name__)

FETCH_BATCH = 1_000

# Tried in order. 18 first because it is current; the fallbacks let an older host
# work without configuration. "SQL Server" is the ancient in-box driver and is last
# because it predates TLS 1.2 defaults.
ODBC_DRIVER_PREFERENCE = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",
)


class DriverUnavailableError(AppError):
    code = "SQLSERVER_DRIVER_UNAVAILABLE"
    status_code = 500


@dataclass
class ConnectionParams:
    host: str
    port: int
    database: str
    username: str = ""
    # Absent under Windows authentication, where the process's own identity is the
    # credential. Not an empty string by accident: "no password" and "the password
    # is blank" are different, and only one of them is valid.
    password: str | None = None
    encrypt: bool = True
    trust_server_certificate: bool = False
    login_timeout: int = 10
    auth_mode: str = SqlAuthMode.SQL.value

    @property
    def is_windows_auth(self) -> bool:
        return self.auth_mode == SqlAuthMode.WINDOWS.value


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


def _odbc_driver():  # noqa: ANN202
    """``pyodbc``, imported lazily like its sibling.

    Lazy for a reason beyond symmetry: on Linux, importing ``pyodbc`` fails unless
    unixODBC is present, and this project's image does not install it. Deferring the
    import to the moment a Windows-auth connection is actually attempted keeps the
    container able to boot, import and serve every other route.
    """
    try:
        import pyodbc
    except ImportError as exc:
        raise DriverUnavailableError(
            "Windows authentication needs the pyodbc driver, which is not installed "
            "in this environment. Install it, or use SQL authentication with a "
            "username and password.",
            code="SQLSERVER_ODBC_DRIVER_UNAVAILABLE",
        ) from exc
    return pyodbc


def _installed_odbc_driver(pyodbc) -> str:  # noqa: ANN001
    """The best installed ODBC driver, or a typed error naming the fix."""
    installed = set(pyodbc.drivers())
    for candidate in ODBC_DRIVER_PREFERENCE:
        if candidate in installed:
            return candidate
    raise DriverUnavailableError(
        "No SQL Server ODBC driver is installed, so Windows authentication cannot "
        "be used. Install 'ODBC Driver 18 for SQL Server'.",
        code="SQLSERVER_ODBC_DRIVER_MISSING",
        details={"installed": sorted(installed)},
    )


def _odbc_connection_string(params: ConnectionParams, driver: str) -> str:
    """The ODBC string for an integrated-authentication login.

    ``Trusted_Connection=yes`` is the whole point: it tells the driver to
    authenticate as the process's own Windows identity, which is why no password
    appears here and none is stored.

    No user-supplied value is quoted or escaped into this string beyond the host,
    port and database the form already constrains, and none of them can contain a
    semicolon - the schema bounds their length and the fields are validated. A
    password is never interpolated because there is not one.
    """
    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={params.host},{params.port}",
        f"DATABASE={params.database}",
        "Trusted_Connection=yes",
        f"Encrypt={'yes' if params.encrypt else 'no'}",
        f"TrustServerCertificate={'yes' if params.trust_server_certificate else 'no'}",
        f"Connection Timeout={params.login_timeout}",
        "APP=RCA-Platform",
    ]
    return ";".join(parts)


def _type_code(raw: Any) -> int | None:
    """``cursor.description[1]``, normalised across the two drivers.

    ``pymssql`` reports an integer type code; ``pyodbc`` reports a Python type
    object such as ``str``. The wire contract is an optional int, so anything that
    is not already one becomes None rather than being coerced into a number that
    would mean nothing.
    """
    return raw if isinstance(raw, int) else None


def sanitize_db_error(exc: Exception) -> str:
    """Strip anything credential-shaped out of a driver error message.

    Driver errors routinely embed the full connection string.
    """
    return redact(str(exc))[:500]


def _connect_sql_auth(params: ConnectionParams) -> Any:
    """Username and password, via pymssql. Unchanged from before the split."""
    pymssql = _driver()
    return pymssql.connect(
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


def _connect_windows_auth(params: ConnectionParams) -> Any:
    """The process's own Windows identity, via pyodbc.

    ``autocommit=False`` for the same reason as the other path: the rollback in
    ``connect``'s ``finally`` is what makes a guard bypass unable to persist
    anything, and it only means something inside a transaction.
    """
    pyodbc = _odbc_driver()
    driver = _installed_odbc_driver(pyodbc)
    return pyodbc.connect(
        _odbc_connection_string(params, driver),
        timeout=params.login_timeout,
        autocommit=False,
    )


@contextmanager
def connect(params: ConnectionParams) -> Iterator[Any]:
    try:
        if params.is_windows_auth:
            connection = _connect_windows_auth(params)
        else:
            connection = _connect_sql_auth(params)
    except AppError:
        # Already typed - a missing driver names its own fix, and re-wrapping it as
        # CONNECT_FAILED would bury that.
        raise
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
                {"name": column[0], "sql_type_code": _type_code(column[1])}
                for column in description
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
