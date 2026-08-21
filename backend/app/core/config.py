"""Application settings.

Every value is overridable through the environment (see ``.env.example``).
"""

from functools import lru_cache
from uuid import UUID

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Fixed identifiers for the company/user seeded by migration 0002. Phase 1 ships
# without authentication, so these stand in for the authenticated principal.
DEFAULT_COMPANY_ID = UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_USER_ID = UUID("00000000-0000-0000-0000-000000000002")

# Obviously-fake key used only when APP_ENV=development. Startup fails if this
# value survives into any other environment.
DEV_ENCRYPTION_KEY = "dev-only-not-a-real-key_TRZ2VuZXJhdGVBUmVhbEs="


class Settings(BaseSettings):
    # --- application -----------------------------------------------------
    app_env: str = "development"
    app_name: str = "AI Root Cause Analysis Platform"
    log_level: str = "INFO"
    log_json: bool = False
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- database --------------------------------------------------------
    database_url: str = "postgresql+psycopg://postgres:12345678@db:5432/rca"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False
    run_migrations: bool = False

    # --- request context (no auth in phase 1) ----------------------------
    default_company_id: UUID = DEFAULT_COMPANY_ID
    default_company_name: str = "Default Company"
    default_user_id: UUID = DEFAULT_USER_ID
    default_user_email: str = "analyst@example.com"
    default_user_name: str = "Default Analyst"

    # --- storage ---------------------------------------------------------
    storage_backend: str = "local"
    storage_local_root: str = "/data/uploads"
    storage_tmp_dir: str = "/data/tmp"

    # --- uploads ---------------------------------------------------------
    max_upload_bytes: int = 209_715_200  # 200 MB (PRD section 6)
    upload_chunk_bytes: int = 1_048_576  # 1 MB (PRD section 6)
    allowed_upload_extensions: list[str] = [".csv", ".tsv", ".txt", ".xlsx"]
    # xlsx is zip-compressed and cannot be streamed for parsing, so it gets a
    # tighter cap than the PRD's CSV-oriented 200 MB.
    excel_max_bytes: int = 26_214_400  # 25 MB
    excel_max_rows: int = 1_000_000
    reject_duplicate_uploads: bool = False

    # --- profiling -------------------------------------------------------
    profiling_async: bool = True
    profiling_stale_minutes: int = 30
    materialize_parquet: bool = True
    profile_top_k: int = 20
    profile_percentiles: list[float] = [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]
    duplicate_check_max_columns: int = 200
    exact_quantile_row_limit: int = 5_000_000
    duckdb_memory_limit: str = "512MB"
    duckdb_threads: int = 2
    duckdb_temp_dir: str = "/data/tmp/duckdb"

    # --- root cause analysis ---------------------------------------------
    # Safety cap on rows returned by one breakdown query. Display truncation is
    # a separate, smaller limit in app.analysis.rca.constants.
    rca_max_segments_scanned: int = 5_000

    # --- anomaly detection -----------------------------------------------
    # Safety cap on the number of calendar periods one series may span. A daily
    # KPI over twenty years is ~7,300 points; past this the grain is almost
    # certainly mis-detected. Statistical thresholds are not tunable here - they
    # live in app.analysis.anomaly.constants where they can be justified.
    anomaly_max_periods: int = 5_000

    # --- secrets ---------------------------------------------------------
    encryption_key: SecretStr | None = None
    encryption_keys_legacy: list[str] = []

    # --- sql server ------------------------------------------------------
    sql_default_row_limit: int = 1_000
    sql_max_row_limit: int = 5_000
    sql_default_timeout_seconds: int = 30
    sql_max_timeout_seconds: int = 120
    sql_dataset_max_rows: int = 1_000_000
    sql_dataset_timeout_seconds: int = 600
    sql_max_statement_length: int = 20_000
    sqlserver_connect_timeout: int = 10
    sqlserver_charset: str = "UTF-8"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("cors_origins", "allowed_upload_extensions", "encryption_keys_legacy", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept either a JSON list or a plain comma-separated string."""
        if isinstance(value, str):
            text = value.strip()
            if not text.startswith("["):
                return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @field_validator("allowed_upload_extensions")
    @classmethod
    def _normalise_extensions(cls, value: list[str]) -> list[str]:
        return [ext if ext.startswith(".") else f".{ext}" for ext in (e.lower() for e in value)]

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in {"development", "dev", "local", "test"}

    @model_validator(mode="after")
    def _require_real_encryption_key(self) -> "Settings":
        """SQL Server passwords are encrypted at rest; refuse a weak key in prod."""
        if self.is_development:
            return self
        key = self.encryption_key.get_secret_value() if self.encryption_key else ""
        if not key or key == DEV_ENCRYPTION_KEY:
            raise ValueError(
                "ENCRYPTION_KEY must be set to a real Fernet key when APP_ENV is not development. "
                'Generate one with: python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
