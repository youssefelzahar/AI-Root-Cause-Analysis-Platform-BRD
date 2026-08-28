/**
 * Mirrors the backend Pydantic schemas (snake_case throughout, matching the
 * API exactly - no camelCase mapping layer to keep in sync).
 *
 * Regenerate from the live OpenAPI schema with `npm run types:api` once the
 * backend is running, to catch drift.
 */

export type Page<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};

export type DatasetStatus =
  | "pending_upload"
  | "uploaded"
  | "validating"
  | "profiling"
  | "profiled"
  | "analysis_ready"
  | "upload_failed"
  | "profiling_failed"
  | "blocked";

export type ValidationState = "pass" | "warning" | "blocked";
export type IssueSeverity = "info" | "warning" | "error";
export type InferredType = "boolean" | "integer" | "numeric" | "date" | "datetime" | "string";
export type SemanticType = "measure" | "dimension" | "time" | "identifier" | "unknown";
export type Aggregation = "SUM" | "AVG" | "COUNT" | "COUNT_DISTINCT" | "MIN" | "MAX" | "MEDIAN";
export type ComparisonPeriod =
  | "previous_period"
  | "previous_month"
  | "previous_quarter"
  | "previous_year"
  | "custom";

/** Statuses where the pipeline is still working and the client should poll. */
export const IN_PROGRESS_STATUSES: DatasetStatus[] = [
  "pending_upload",
  "uploaded",
  "validating",
  "profiling",
];

export type DatasetSummary = {
  id: string;
  name: string;
  original_filename: string | null;
  source_type: "csv" | "excel" | "sqlserver";
  file_format: "csv" | "tsv" | "xlsx" | "parquet";
  size_bytes: number;
  row_count: number | null;
  column_count: number | null;
  status: DatasetStatus;
  upload_status: "pending" | "uploading" | "stored" | "failed";
  quality_state: ValidationState | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type DatasetDetail = DatasetSummary & {
  description: string | null;
  checksum_sha256: string | null;
  schema_version: number;
  storage_key: string | null;
  source_query: string | null;
  source_connection_id: string | null;
  profiling_started_at: string | null;
  profiling_completed_at: string | null;
  has_profile: boolean;
  has_kpi_definition: boolean;
  analysis_ready: boolean;
};

export type DatasetStatusRead = {
  dataset_id: string;
  status: DatasetStatus;
  upload_status: string;
  quality_state: ValidationState | null;
  profile_ready: boolean;
  validation_ready: boolean;
  analysis_ready: boolean;
  row_count: number | null;
  column_count: number | null;
  error_code: string | null;
  error_message: string | null;
  updated_at: string;
};

export type UploadResponse = {
  dataset: DatasetDetail;
  duplicate_of: string | null;
};

export type TopValue = { value: string; count: number; pct: number | null };

export type DatetimeStats = {
  min_date: string | null;
  max_date: string | null;
  distinct_periods: number | null;
  detected_frequency: string | null;
  frequency_confidence: number | null;
  expected_periods: number | null;
  missing_periods: number | null;
};

export type ColumnProfile = {
  column_name: string;
  ordinal_position: number;
  raw_type: string;
  inferred_type: InferredType;
  semantic_type: SemanticType;
  conversion_confidence: number | null;
  requires_conversion: boolean;
  invalid_value_count: number;
  sample_invalid_values: string[] | null;
  null_count: number;
  null_pct: number;
  unique_count: number | null;
  unique_pct: number | null;
  min_value: string | null;
  max_value: string | null;
  mean: number | null;
  median: number | null;
  stddev: number | null;
  percentiles: Record<string, number> | null;
  outlier_count: number | null;
  outlier_lower: number | null;
  outlier_upper: number | null;
  top_values: TopValue[] | null;
  datetime_stats: DatetimeStats | null;
  kpi_measure_score: number | null;
  kpi_dimension_score: number | null;
  kpi_time_score: number | null;
  suggested_aggregation: Aggregation | null;
  candidate_reasons: Record<string, string[]> | null;
};

export type DatasetProfile = {
  dataset_id: string;
  profile_version: number;
  row_count: number;
  column_count: number;
  file_size_bytes: number;
  duplicate_row_count: number | null;
  duplicate_row_pct: number | null;
  duplicate_check_skipped: boolean;
  missing_cell_count: number;
  missing_cell_pct: number;
  quality_status: ValidationState | null;
  engine: string;
  exact_quantiles: boolean;
  duration_ms: number | null;
  generated_at: string;
};

export type ProfileEnvelope = {
  state: "pending" | "running" | "ready" | "failed";
  profile: DatasetProfile | null;
  columns: ColumnProfile[];
  message: string | null;
};

export type ValidationIssue = {
  code: string;
  severity: IssueSeverity;
  message: string;
  column: string | null;
  details: Record<string, unknown>;
  suggested_fix: Record<string, unknown> | null;
};

export type SchemaValidation = {
  id: string;
  dataset_id: string;
  kpi_definition_id: string | null;
  mode: "structural" | "analysis";
  state: ValidationState;
  error_count: number;
  warning_count: number;
  info_count: number;
  issues: ValidationIssue[];
  rules_version: string;
  created_at: string;
};

export type KpiCandidate = {
  column: string;
  score: number;
  reasons: string[];
  suggested_aggregation: Aggregation | null;
  dtype: InferredType | null;
  cardinality: number | null;
  detected_frequency: string | null;
  min_date: string | null;
  max_date: string | null;
  distinct_periods: number | null;
};

/** The normalized contract handed to the RCA engine (PRD section 11). */
export type KpiDefinitionPayload = {
  name: string;
  column: string;
  aggregation: Aggregation;
  time_column: string | null;
  dimensions: string[];
  comparison: ComparisonPeriod;
};

export type KpiCandidates = {
  measures: KpiCandidate[];
  time_columns: KpiCandidate[];
  dimensions: KpiCandidate[];
  recommended_default: Partial<KpiDefinitionPayload>;
};

export type KpiDefinition = {
  id: string;
  dataset_id: string;
  name: string;
  column_name: string;
  aggregation: Aggregation;
  time_column: string | null;
  dimensions: string[];
  comparison: ComparisonPeriod;
  definition: KpiDefinitionPayload;
  is_active: boolean;
  validation_state: ValidationState | null;
  created_at: string;
};

export type KpiDefinitionEnvelope = {
  kpi_definition: KpiDefinition;
  validation: { state: ValidationState; issues: ValidationIssue[] };
  analysis_ready: boolean;
};

export type PreviewRead = {
  columns: { name: string; type: string }[];
  rows: (string | null)[][];
  total_rows: number | null;
  limit: number;
  offset: number;
};

/** Note: no password field exists on this type, by design. */
/**
 * How a saved connection authenticates.
 *
 * `windows` is integrated authentication: no password is stored, because the
 * connection borrows the identity of the server process. It therefore only works
 * where that process runs on Windows as the user holding the SQL Server grant.
 */
export type SqlAuthMode = "sql" | "windows";

export type SqlConnection = {
  id: string;
  name: string;
  host: string;
  port: number;
  database_name: string;
  auth_mode: SqlAuthMode;
  /** Empty string under Windows authentication - there is no user to name. */
  username: string;
  encrypt: boolean;
  trust_server_certificate: boolean;
  last_tested_at: string | null;
  last_test_ok: boolean | null;
  last_test_error: string | null;
  created_at: string;
  updated_at: string;
};

export type SqlConnectionTestResult = {
  ok: boolean;
  server_version: string | null;
  database: string | null;
  latency_ms: number | null;
  error_code: string | null;
  message: string | null;
};

export type SqlGuardResult = {
  allowed: boolean;
  statement_type: string | null;
  reasons: string[];
  normalized_sql: string | null;
};

export type SqlExecuteResult = {
  columns: { name: string; sql_type_code: number | null }[];
  rows: (string | null)[][];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
};

export type AppContext = {
  company_id: string;
  company_name: string;
  user_id: string;
  user_name: string;
  user_email: string;
  authenticated: boolean;
};
