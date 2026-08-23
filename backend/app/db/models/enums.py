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


class InvestigationStatus(str, Enum):
    """Lifecycle of one persisted investigation.

    PARTIAL is not a failure: the decomposition succeeded but a planned step was
    skipped or degraded, and the limitations say which. FAILED means there is no
    result at all.
    """

    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"

    @classmethod
    def terminal(cls) -> set["InvestigationStatus"]:
        return {cls.COMPLETED, cls.PARTIAL, cls.FAILED}


class EvidenceType(str, Enum):
    """What kind of claim one evidence record makes."""

    KPI_CHANGE = "kpi_change"
    DIMENSION_CHANGE = "dimension_change"
    CONTRIBUTION = "contribution"
    DRILL_DOWN = "drill_down"
    ANOMALY = "anomaly"
    TREND = "trend"
    COMPARISON = "comparison"
    VALIDATION = "validation"
    NEW_SEGMENT = "new_segment"
    GONE_SEGMENT = "gone_segment"
    OFFSETTING_FACTOR = "offsetting_factor"
    EXECUTION = "execution"
    COVERAGE = "coverage"
    RECONCILIATION = "reconciliation"


class EvidenceValidationStatus(str, Enum):
    """Whether one record survived the validator.

    The builder emits UNVERIFIED; the validator pass promotes or demotes it, so
    a record left UNVERIFIED means the validator never reached it.
    """

    VALIDATED = "validated"
    UNVERIFIED = "unverified"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class EvidenceConfidence(str, Enum):
    """How much weight one record's number deserves."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EvidenceQuality(str, Enum):
    """The verdict on whether an analysis is well-formed and traceable.

    Deliberately not ``ValidationState``: that one judges a dataset's schema,
    this one judges whether an analytical claim can be trusted. Same subject
    separation as ``anomaly.models.Severity`` not reusing ``rca.ranking``'s.
    """

    VALIDATED = "validated"
    WARNING = "warning"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class QualityCheckStatus(str, Enum):
    """One of the six evidence-quality checks."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class ReconciliationStatus(str, Enum):
    """Whether the complete decomposition accounts for the whole movement.

    NOT_APPLICABLE is load-bearing: a MEDIAN cannot be decomposed at all, and a
    missing decomposition is not a failed one.
    """

    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class TreeDriftStatus(str, Enum):
    """Whether the drill-down tree's children sum to their parents.

    Three states because two causes of drift are legitimate - a truncated level
    and a pure split - and only the third is a lost-rows bug.
    """

    PASSED = "passed"
    DRIFT_EXPLAINED = "drift_explained"
    DRIFT_UNEXPLAINED = "drift_unexplained"


class AuditEventType(str, Enum):
    """What happened during an investigation, in order."""

    INVESTIGATION_STARTED = "investigation_started"
    PERIODS_RESOLVED = "periods_resolved"
    KPI_CALCULATED = "kpi_calculated"
    DIMENSION_ANALYSIS_EXECUTED = "dimension_analysis_executed"
    CONTRIBUTOR_SELECTED = "contributor_selected"
    DRILLDOWN_EXECUTED = "drilldown_executed"
    DRILLDOWN_STOPPED = "drilldown_stopped"
    ANOMALY_DETECTION_EXECUTED = "anomaly_detection_executed"
    ANOMALY_DETECTION_SKIPPED = "anomaly_detection_skipped"
    EVIDENCE_BUILT = "evidence_built"
    EVIDENCE_VALIDATED = "evidence_validated"
    RECONCILIATION_PASSED = "reconciliation_passed"
    RECONCILIATION_FAILED = "reconciliation_failed"
    INVESTIGATION_COMPLETED = "investigation_completed"
    INVESTIGATION_PARTIAL = "investigation_partial"
    INVESTIGATION_FAILED = "investigation_failed"


class QueryStatus(str, Enum):
    """Whether one traced statement ran."""

    OK = "ok"
    FAILED = "failed"


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
