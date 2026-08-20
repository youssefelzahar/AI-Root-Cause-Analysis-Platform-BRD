"""Open a stored dataset as a queryable DuckDB relation.

The storage -> xlsx -> DuckDB dance has three easy ways to go wrong, so it lives
in one place rather than being repeated per caller:

* ``normalized_key`` must be preferred, and when it is present the format is
  Parquet regardless of ``dataset.file_format``.
* ``local_path()`` returns None on any backend without a real filesystem, so
  reads must go through ``as_local_file``.
* ``source_relation`` rejects xlsx outright, so a dataset that was never
  normalized has to be converted first - and the temp file cleaned up after.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import duckdb

from app.analysis.duckdb_session import duckdb_connection, source_relation
from app.core.exceptions import NotReadyError
from app.db.models.dataset import Dataset
from app.db.models.enums import FileFormat
from app.services.materialize import excel_to_parquet, temp_path
from app.services.storage_service import get_storage


@contextmanager
def open_dataset_relation(dataset: Dataset) -> Iterator[tuple[duckdb.DuckDBPyConnection, str]]:
    """Yield ``(connection, relation)`` for a dataset's canonical data.

    ``relation`` is a DuckDB table-function expression to interpolate after
    ``FROM``. Each call gets a fresh in-memory database, so temp tables created
    by the caller are private to this request and concurrent readers cannot
    collide.
    """
    if not dataset.storage_key:
        raise NotReadyError("This dataset has no stored data yet.", code="DATA_NOT_READY")

    storage = get_storage()
    key = dataset.normalized_key or dataset.storage_key
    read_format = FileFormat.PARQUET.value if dataset.normalized_key else dataset.file_format

    temp = None
    try:
        with storage.as_local_file(key) as path:
            read_path = path
            if read_format == FileFormat.XLSX.value:
                temp = temp_path(".parquet")
                excel_to_parquet(path, temp)
                read_path, read_format = temp, FileFormat.PARQUET.value

            with duckdb_connection() as conn:
                yield conn, source_relation(read_path, read_format)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def preview_rows(
    dataset: Dataset, *, limit: int, offset: int
) -> tuple[list[tuple[str, str]], list[list[str | None]]]:
    """Column types and a page of rows, rendered as text for display."""
    with open_dataset_relation(dataset) as (conn, relation):
        described = conn.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
        rows = conn.execute(
            f"SELECT * FROM {relation} LIMIT {int(limit)} OFFSET {int(offset)}"
        ).fetchall()

    columns = [(row[0], str(row[1])) for row in described]
    text_rows = [[None if value is None else str(value) for value in row] for row in rows]
    return columns, text_rows


def dataset_columns(dataset: Dataset) -> dict[str, str]:
    """Physical column types of the canonical data, for schema-drift checks."""
    with open_dataset_relation(dataset) as (conn, relation):
        described = conn.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    return {row[0]: str(row[1]) for row in described}


__all__ = ["dataset_columns", "open_dataset_relation", "preview_rows"]
