"""Domain enumerations.

These are plain ``str`` enums stored as ``String`` columns guarded by CHECK
constraints rather than native PostgreSQL ENUM types. Adding a value stays a
data-only change instead of an ``ALTER TYPE`` migration.
"""

from enum import Enum


class SourceType(str, Enum):
    CSV = "csv"
    EXCEL = "excel"
    SQLSERVER = "sqlserver"


class FileFormat(str, Enum):
    CSV = "csv"
    TSV = "tsv"
    XLSX = "xlsx"
    PARQUET = "parquet"


class UploadStatus(str, Enum):
    """Physical lifecycle of the stored bytes (PRD section 7)."""

    PENDING = "pending"
    UPLOADING = "uploading"
    STORED = "stored"
    FAILED = "failed"


class DatasetStatus(str, Enum):
    """Pipeline state driving the UI."""

    PENDING_UPLOAD = "pending_upload"
    UPLOADED = "uploaded"
    VALIDATING = "validating"
    PROFILING = "profiling"
    PROFILED = "profiled"
    ANALYSIS_READY = "analysis_ready"
    UPLOAD_FAILED = "upload_failed"
    PROFILING_FAILED = "profiling_failed"
    BLOCKED = "blocked"

    @classmethod
    def terminal(cls) -> set["DatasetStatus"]:
        return {cls.PROFILED, cls.ANALYSIS_READY, cls.UPLOAD_FAILED, cls.PROFILING_FAILED, cls.BLOCKED}

    @classmethod
    def in_progress(cls) -> set["DatasetStatus"]:
        return {cls.PENDING_UPLOAD, cls.UPLOADED, cls.VALIDATING, cls.PROFILING}


class ValidationState(str, Enum):
    """PRD section 10."""

    PASS = "pass"
    WARNING = "warning"
    BLOCKED = "blocked"


class ValidationMode(str, Enum):
    STRUCTURAL = "structural"
    ANALYSIS = "analysis"


class IssueSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class InferredType(str, Enum):
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMERIC = "numeric"
    DATE = "date"
    DATETIME = "datetime"
    STRING = "string"


class SemanticType(str, Enum):
    """What a column can be used for in an analysis (PRD section 11)."""

    MEASURE = "measure"
    DIMENSION = "dimension"
    TIME = "time"
    IDENTIFIER = "identifier"
    UNKNOWN = "unknown"


class Aggregation(str, Enum):
    SUM = "SUM"
    AVG = "AVG"
    COUNT = "COUNT"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    MIN = "MIN"
    MAX = "MAX"
    MEDIAN = "MEDIAN"


class ComparisonPeriod(str, Enum):
    PREVIOUS_PERIOD = "previous_period"
    PREVIOUS_MONTH = "previous_month"
    PREVIOUS_QUARTER = "previous_quarter"
    PREVIOUS_YEAR = "previous_year"
    CUSTOM = "custom"


NUMERIC_TYPES = {InferredType.INTEGER, InferredType.NUMERIC}
TEMPORAL_TYPES = {InferredType.DATE, InferredType.DATETIME}

# Aggregations that require an actually-numeric column.
NUMERIC_AGGREGATIONS = {
    Aggregation.SUM,
    Aggregation.AVG,
    Aggregation.MIN,
    Aggregation.MAX,
    Aggregation.MEDIAN,
}
