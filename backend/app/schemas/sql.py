from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.db.models.enums import SqlAuthMode


class SqlConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=1433, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    # Defaulted, so every existing client keeps working without sending it.
    auth_mode: SqlAuthMode = SqlAuthMode.SQL
    # Both optional at the field level and required by mode below, because which
    # of them is mandatory depends on auth_mode - a thing a single field cannot say.
    username: str = Field(default="", max_length=128)
    # SecretStr keeps the value out of reprs, validation errors and the
    # generated OpenAPI examples.
    password: SecretStr | None = None
    encrypt: bool = True
    trust_server_certificate: bool = False

    @model_validator(mode="after")
    def _credentials_match_auth_mode(self) -> "SqlConnectionCreate":
        """Reject a request whose credentials do not match its mode.

        Windows auth is rejected rather than quietly ignoring a supplied password,
        because a caller who sent one believes it is being used - and silently
        dropping a credential someone thinks is protecting something is worse than
        refusing the request.
        """
        if self.auth_mode is SqlAuthMode.WINDOWS:
            if self.password is not None and self.password.get_secret_value():
                raise ValueError(
                    "Windows authentication uses the identity of the server process, "
                    "so it takes no password. Remove it, or choose SQL authentication."
                )
            if self.username.strip():
                raise ValueError(
                    "Windows authentication takes no username: the login is whoever "
                    "the server process runs as."
                )
            return self
        if not self.username.strip():
            raise ValueError("SQL authentication needs a username.")
        if self.password is None or not self.password.get_secret_value():
            raise ValueError("SQL authentication needs a password.")
        return self

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "auth_mode": self.auth_mode.value,
            "username": self.username.strip(),
            "password": self.password.get_secret_value() if self.password else None,
            "encrypt": self.encrypt,
            "trust_server_certificate": self.trust_server_certificate,
        }


class SqlConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, max_length=128)
    # Deliberately absent: switching a saved connection between authentication
    # modes would mean adding or discarding a credential under a PATCH, and the
    # database's own CHECK forbids the half-applied state that a partial update
    # could produce. Delete it and create the connection you want instead.
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
    auth_mode: str
    # Empty string under Windows authentication, where there is no user to name.
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
