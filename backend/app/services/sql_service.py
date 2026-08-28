"""SQL Server connection management and the SQL editor (PRD section 8)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors import sqlserver
from app.connectors.sql_guard import check_sql
from app.core.config import settings
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.security import decrypt_secret, encrypt_secret
from app.db.models import Dataset, SqlConnection
from app.db.models.enums import (
    DatasetStatus,
    FileFormat,
    SourceType,
    SqlAuthMode,
    UploadStatus,
)
from app.services.materialize import rows_to_parquet, temp_path
from app.services.storage_service import get_storage

logger = get_logger(__name__)


class StatementNotReadOnlyError(AppError):
    code = "STATEMENT_NOT_READ_ONLY"
    status_code = 400


def _params(connection: SqlConnection) -> sqlserver.ConnectionParams:
    return sqlserver.ConnectionParams(
        host=connection.host,
        port=connection.port,
        database=connection.database_name,
        auth_mode=connection.auth_mode,
        username=connection.username,
        # Only decrypted when there is something to decrypt. A Windows-auth row
        # stores no token, and calling decrypt on NULL would fail with an
        # encryption error rather than the honest "this mode has no password".
        password=(
            decrypt_secret(connection.password_encrypted)
            if connection.password_encrypted
            else None
        ),
        encrypt=connection.encrypt,
        trust_server_certificate=connection.trust_server_certificate,
        login_timeout=settings.sqlserver_connect_timeout,
    )


def create_connection(
    db: Session, payload: dict[str, Any], *, company_id: uuid.UUID, user_id: uuid.UUID | None
) -> SqlConnection:
    existing = db.scalar(
        select(SqlConnection).where(
            SqlConnection.company_id == company_id, SqlConnection.name == payload["name"]
        )
    )
    if existing is not None:
        raise ConflictError(
            f"A connection named '{payload['name']}' already exists.", code="CONNECTION_NAME_TAKEN"
        )

    password = payload.get("password")
    connection = SqlConnection(
        company_id=company_id,
        created_by=user_id,
        name=payload["name"],
        host=payload["host"],
        port=payload.get("port", 1433),
        database_name=payload["database"],
        auth_mode=payload.get("auth_mode", SqlAuthMode.SQL.value),
        username=payload.get("username") or "",
        # Encrypted immediately; the plaintext never reaches the database. NULL
        # under Windows auth, which stores no credential at all - the schema's own
        # CHECK holds that invariant.
        password_encrypted=encrypt_secret(password) if password else None,
        encrypt=payload.get("encrypt", True),
        trust_server_certificate=payload.get("trust_server_certificate", False),
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


def get_connection(db: Session, connection_id: uuid.UUID, company_id: uuid.UUID) -> SqlConnection:
    connection = db.scalar(
        select(SqlConnection).where(
            SqlConnection.id == connection_id, SqlConnection.company_id == company_id
        )
    )
    if connection is None:
        raise NotFoundError("SQL connection not found.", code="CONNECTION_NOT_FOUND")
    return connection


def update_connection(db: Session, connection: SqlConnection, payload: dict[str, Any]) -> SqlConnection:
    for field_name, column in (
        ("name", "name"),
        ("host", "host"),
        ("port", "port"),
        ("database", "database_name"),
        ("username", "username"),
        ("encrypt", "encrypt"),
        ("trust_server_certificate", "trust_server_certificate"),
    ):
        if payload.get(field_name) is not None:
            setattr(connection, column, payload[field_name])
    # Only re-encrypt when a new password was actually supplied.
    if payload.get("password"):
        if connection.auth_mode == SqlAuthMode.WINDOWS.value:
            # Accepting it would store a credential the connector will never send,
            # and leave the row failing its own CHECK.
            raise ConflictError(
                "This connection uses Windows authentication, which takes no password.",
                code="AUTH_MODE_TAKES_NO_PASSWORD",
            )
        connection.password_encrypted = encrypt_secret(payload["password"])
    db.commit()
    db.refresh(connection)
    return connection


def test_connection(db: Session, connection: SqlConnection) -> dict[str, Any]:
    result = sqlserver.test_connection(_params(connection))
    connection.last_tested_at = datetime.now(UTC)
    connection.last_test_ok = bool(result.get("ok"))
    connection.last_test_error = None if result.get("ok") else str(result.get("message"))[:500]
    db.commit()
    return result


def test_unsaved(payload: dict[str, Any]) -> dict[str, Any]:
    return sqlserver.test_connection(
        sqlserver.ConnectionParams(
            host=payload["host"],
            port=payload.get("port", 1433),
            database=payload["database"],
            auth_mode=payload.get("auth_mode", SqlAuthMode.SQL.value),
            username=payload.get("username") or "",
            password=payload.get("password"),
            encrypt=payload.get("encrypt", True),
            trust_server_certificate=payload.get("trust_server_certificate", False),
            login_timeout=settings.sqlserver_connect_timeout,
        )
    )


def guard_or_raise(sql: str) -> None:
    result = check_sql(sql)
    if not result.allowed:
        raise StatementNotReadOnlyError(
            "Only single read-only SELECT statements can be executed.",
            details={"reasons": result.reasons, "statement_type": result.statement_type},
        )


def execute(
    db: Session, connection: SqlConnection, sql: str, *, row_limit: int, timeout_seconds: int
) -> sqlserver.QueryResult:
    # Guard before opening any connection - a rejected statement must never
    # reach the server.
    guard_or_raise(sql)
    row_limit = min(row_limit or settings.sql_default_row_limit, settings.sql_max_row_limit)
    timeout_seconds = min(
        timeout_seconds or settings.sql_default_timeout_seconds, settings.sql_max_timeout_seconds
    )
    return sqlserver.run_query(
        _params(connection), sql, row_limit=row_limit, timeout_seconds=timeout_seconds
    )


def save_query_as_dataset(
    db: Session,
    connection: SqlConnection,
    *,
    sql: str,
    dataset_name: str,
    description: str | None,
    company_id: uuid.UUID,
    user_id: uuid.UUID | None,
    max_rows: int | None = None,
) -> Dataset:
    """Materialise a query result as an internal dataset (PRD section 8).

    Rows stream into Parquet in batches, so a large result never becomes a
    large Python list.
    """
    guard_or_raise(sql)
    max_rows = min(max_rows or settings.sql_dataset_max_rows, settings.sql_dataset_max_rows)

    dataset = Dataset(
        company_id=company_id,
        user_id=user_id,
        name=dataset_name,
        description=description,
        source_type=SourceType.SQLSERVER.value,
        original_filename=None,
        file_format=FileFormat.PARQUET.value,
        source_connection_id=connection.id,
        # The SELECT text is not a secret; credentials stay on the connection.
        source_query=sql,
        upload_status=UploadStatus.UPLOADING.value,
        status=DatasetStatus.PENDING_UPLOAD.value,
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)

    output_path = temp_path(".parquet")
    try:
        columns: list[str] = []
        batches: list[list[list[Any]]] = []

        def generate():
            nonlocal columns
            for column_names, batch in sqlserver.iter_rows(
                _params(connection),
                sql,
                max_rows=max_rows,
                timeout_seconds=settings.sql_dataset_timeout_seconds,
            ):
                columns = column_names
                yield batch

        # The first batch is pulled eagerly so the column names are known
        # before the Parquet schema is created.
        generator = generate()
        first = next(generator, None)
        if first is None:
            columns = columns or ["result"]
            batches = []
        else:
            batches = [first]

        def all_batches():
            yield from batches
            yield from generator

        total = rows_to_parquet(columns, all_batches(), output_path, max_rows=max_rows)

        storage = get_storage()
        key = f"{company_id}/sql/{dataset.id}.parquet"
        stored = storage.save_file(key, output_path, move=True)

        dataset.storage_key = stored.key
        dataset.normalized_key = stored.key
        dataset.size_bytes = stored.size_bytes
        dataset.checksum_sha256 = stored.sha256
        dataset.row_count = total
        dataset.column_count = len(columns)
        dataset.upload_status = UploadStatus.STORED.value
        dataset.status = DatasetStatus.UPLOADED.value
        db.commit()
        db.refresh(dataset)
        return dataset
    except Exception as exc:
        db.rollback()
        record = db.get(Dataset, dataset.id)
        if record is not None:
            record.upload_status = UploadStatus.FAILED.value
            record.status = DatasetStatus.UPLOAD_FAILED.value
            record.error_code = getattr(exc, "code", type(exc).__name__)
            record.error_message = str(exc)[:2000]
            db.commit()
        raise
    finally:
        output_path.unlink(missing_ok=True)
