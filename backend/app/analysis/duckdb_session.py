"""Configured DuckDB connections.

DuckDB is the profiling engine because it reads a 200 MB CSV out-of-core and
computes every statistic the PRD asks for in a single scan, without loading the
file into Python memory (PRD principle 1). Memory is bounded by configuration
rather than by file size.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@contextmanager
def duckdb_connection() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(database=":memory:")
    try:
        conn.execute(f"PRAGMA memory_limit='{settings.duckdb_memory_limit}'")
        conn.execute(f"PRAGMA threads={settings.duckdb_threads}")
        temp_dir = Path(settings.duckdb_temp_dir)
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
            conn.execute(f"SET temp_directory='{temp_dir.as_posix()}'")
        except OSError:
            # Fall back to DuckDB's default rather than failing the profile.
            logger.warning("duckdb_temp_dir_unavailable", extra={"path": str(temp_dir)})
        conn.execute("SET preserve_insertion_order=false")
        yield conn
    finally:
        conn.close()


def quote_identifier(name: str) -> str:
    """Quote a column name for interpolation into generated SQL.

    Column names come from user files, so they are never concatenated raw.
    """
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def source_relation(path: Path, file_format: str, *, all_varchar: bool = False) -> str:
    """Build the DuckDB table function for a stored file."""
    location = quote_literal(path.as_posix())
    fmt = file_format.lower()
    if fmt == "parquet":
        return f"read_parquet({location})"
    if fmt in {"csv", "tsv", "txt"}:
        parts = [location, "header=true", "sample_size=200000", "ignore_errors=false"]
        if fmt == "tsv":
            parts.append("delim='\\t'")
        if all_varchar:
            parts.append("all_varchar=true")
        return "read_csv(" + ", ".join(parts) + ")"
    raise ValueError(f"Unsupported format for DuckDB: {file_format}")
