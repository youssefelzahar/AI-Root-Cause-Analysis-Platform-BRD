from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class SqlConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=1433, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    # SecretStr keeps the value out of reprs, validation errors and the
    # generated OpenAPI examples.
    password: SecretStr
    encrypt: bool = True
    trust_server_certificate: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "password": self.password.get_secret_value(),
            "encrypt": self.encrypt,
            "trust_server_certificate": self.trust_server_certificate,
        }


class SqlConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, max_length=128)
    username: str | None = Field(default=None, max_length=128)
    password: SecretStr | None = None
    encrypt: bool | None = None
    trust_server_certificate: bool | None = None

    def to_payload(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True)
        if self.password is not None:
            data["password"] = self.password.get_secret_value()
        return data


class SqlConnectionRead(BaseModel):
    """Response model with **no password field at all**.

    Making the omission structural means a credential cannot leak through this
    endpoint even by mistake (PRD section 8).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    host: str
    port: int
    database_name: str
    username: str
    encrypt: bool
    trust_server_certificate: bool
    last_tested_at: datetime | None = None
    last_test_ok: bool | None = None
    last_test_error: str | None = None
    created_at: datetime
    updated_at: datetime


class SqlConnectionTestResult(BaseModel):
    ok: bool
    server_version: str | None = None
    database: str | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    message: str | None = None


class SqlValidateRequest(BaseModel):
    sql: str


class SqlGuardRead(BaseModel):
    allowed: bool
    statement_type: str | None = None
    reasons: list[str] = []
    normalized_sql: str | None = None


class SqlExecuteRequest(BaseModel):
    sql: str = Field(min_length=1)
    row_limit: int | None = Field(default=None, ge=1)
    timeout_seconds: int | None = Field(default=None, ge=1)


class SqlColumnRead(BaseModel):
    name: str
    sql_type_code: int | None = None


class SqlExecuteResult(BaseModel):
    columns: list[SqlColumnRead]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int


class SaveQueryAsDatasetRequest(BaseModel):
    sql: str = Field(min_length=1)
    dataset_name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    max_rows: int | None = Field(default=None, ge=1)
