/**
 * Persisted, evidence-backed investigations.
 *
 * Mirrors `backend/app/schemas/investigation.py` model for model, snake_case
 * throughout, with no mapping layer - the same convention as `types/rca.ts`.
 * That one-to-one correspondence is what makes `npm run types:api` a usable
 * drift check.
 *
 * A new file rather than an addition to `types/rca.ts`: this is a different
 * resource under a different prefix. `/api/rca/investigations` is *the analysis*
 * - stateless, recomputed on every call. `/api/investigations` is *the
 * investigation* - persisted, addressable, and a link to one is a snapshot.
 */

import type { Driver, InvestigationResult } from "@/types/rca";

/* ------------------------------------------------------------- lifecycle */

/**
 * `partial` is not a failure: the decomposition succeeded but a planned step was
 * skipped or degraded, and `limitations` says which. `failed` means there is no
 * result at all.
 */
export type InvestigationStatus =
  | "planned"
  | "running"
  | "completed"
  | "partial"
  | "failed";

/* ------------------------------------------------------ evidence quality */

/**
 * Whether the analysis is well-formed and traceable.
 *
 * Deliberately not the same vocabulary as dataset validation (`pass` /
 * `warning` / `blocked`): that judges a file's schema, this judges whether an
 * analytical claim can be trusted.
 */
export type EvidenceQualityVerdict =
  | "validated"
  | "warning"
  | "failed"
  | "not_applicable";

export type QualityCheckStatus = "passed" | "warning" | "failed" | "not_applicable";

/** The six checks, in the order the backend always returns them. */
export type QualityCheckId =
  | "data_period_coverage"
  | "numerical_consistency"
  | "contribution_reconciliation"
  | "query_provenance"
  | "source_traceability"
  | "required_metadata";

export type QualityCheck = {
  check: QualityCheckId | string;
  status: QualityCheckStatus;
  /** The engine's own words. Never synthesised in the UI. */
  detail: string;
  /** The numbers the check asserted on, so a reader can redo it. */
  inputs: Record<string, unknown>;
};

export type EvidenceQuality = {
  verdict: EvidenceQualityVerdict | null;
  checks: QualityCheck[];
  /**
   * Coverage caveats. They qualify a verdict without degrading it: thin data is
   * a finding about the data, not about the analysis.
   */
  caveats: string[];
};

/* -------------------------------------------------------- reconciliation */

/**
 * `not_applicable` is load-bearing: a MEDIAN cannot be decomposed at all, and a
 * missing decomposition is not a failed one.
 */
export type ReconciliationStatus = "passed" | "failed" | "not_applicable";

/** Two causes of tree drift are legitimate; only the third is a bug. */
export type TreeDriftStatus = "passed" | "drift_explained" | "drift_unexplained";

export type Reconciliation = {
  status: ReconciliationStatus | null;
  contribution_sum: number | null;
  /** The tolerance actually applied. The UI never applies one of its own. */
  tolerance: number | null;
  tree_drift_status: TreeDriftStatus | null;
};

/* ------------------------------------------------------------- execution */

export type Execution = {
  rows_scanned: number;
  rows_in_previous_period: number;
  rows_in_current_period: number;
  rows_outside_periods: number;
  queries_executed: number;
  execution_time_ms: number;
};

/* -------------------------------------------------------- decision trace */

export type DecisionKind =
  | "period_resolved"
  | "basis_selected"
  | "dimension_selected"
  | "segment_selected"
  | "drilldown_stopped"
  | "pattern_classified"
  | "driver_suppressed";

export type Decision = {
  sequence: number;
  kind: DecisionKind | string;
  subject: string;
  outcome: string;
  reason_code: string;
  /** A sentence for a reader. */
  why: string;
  dimension: string | null;
  depth: number;
  /** Assembled from user data - encode before using as a DOM id. */
  node_id: string | null;
  /** The numbers the decision was made on, so the sentence is checkable. */
  inputs: Record<string, unknown>;
  evidence_ids: string[];
};

/* ------------------------------------------------------- evidence record */

export type EvidenceType =
  | "kpi_change"
  | "dimension_change"
  | "contribution"
  | "drill_down"
  | "anomaly"
  | "trend"
  | "comparison"
  | "validation"
  | "new_segment"
  | "gone_segment"
  | "offsetting_factor"
  | "execution"
  | "coverage"
  | "reconciliation";

export type EvidenceValidationStatus =
  | "validated"
  | "unverified"
  | "failed"
  | "not_applicable";

export type EvidenceConfidence = "high" | "medium" | "low";

export type EvidenceRecord = {
  id: string;
  sequence: number;
  evidence_type: EvidenceType | string;
  claim: string;
  metric: string | null;
  dimension: string | null;
  dimension_value: string | null;
  dimension_value_is_null: boolean;
  previous_period: string | null;
  current_period: string | null;
  previous_value: number | null;
  current_value: number | null;
  absolute_change: number | null;
  percentage_change: number | null;
  contribution_percentage: number | null;
  /**
   * How far the segments deviated from moving in proportion to their size - not
   * a share of anything, so it can legitimately exceed 100%. Never render it
   * beside a contribution as if the two were the same kind of number.
   */
  explanatory_power: number | null;
  filters: Array<Record<string, unknown>> | null;
  source_dataset: string;
  source_relation: string;
  source_columns: string[];
  /** The statement that produced these numbers, or null. Never invented. */
  query: string | null;
  query_sequence: number | null;
  analysis_tool: string;
  validation_status: EvidenceValidationStatus | string;
  confidence: EvidenceConfidence | string | null;
  node_id: string | null;
  depth: number | null;
  classification: string | null;
  rank: number;
  details: Record<string, unknown> | null;
};

/* ----------------------------------------------------------- query trace */

export type QueryStatus = "ok" | "failed";

/**
 * One executed statement. There is no `parameters` field by construction: a
 * bound value can be a customer name, so only the count is ever recorded.
 */
export type QueryTraceEntry = {
  id: string;
  sequence: number;
  purpose: string;
  sql: string;
  parameter_count: number;
  rows_returned: number | null;
  duration_ms: number;
  status: QueryStatus | string;
  error: string | null;
  depth: number | null;
  node_id: string | null;
};

/* ----------------------------------------------------------- audit trail */

export type AuditEvent = {
  id: string;
  sequence: number;
  event_type: string;
  message: string;
  /** A monotonic offset from the run's start - the reproducible field. */
  elapsed_ms: number;
  occurred_at: string;
  details: Record<string, unknown> | null;
};

/* ------------------------------------------------------------ the source */

export type InvestigationSource = {
  dataset_id: string;
  dataset_name: string;
  kpi_definition_id: string | null;
  /** The storage-key form, not a server temp path. */
  source_relation: string;
  measure_column: string;
  time_column: string | null;
  aggregation: string;
  comparison: string;
};

/* ------------------------------------------------- the tree as a graph */

/**
 * A tree node, with the evidence behind it.
 *
 * A strict superset of `Driver`: the backend emits every `DriverRead` field in
 * the same shape, plus `node_key` and the ids of the evidence and decisions that
 * produced it. Declared as an intersection rather than duplicated, so a change
 * to `Driver` cannot silently drift from this.
 */
export type EvidenceTreeNode = Omit<Driver, "children"> & {
  /** Short, opaque and DOM-safe. `node_id` is not: it carries user data. */
  node_key: string;
  evidence_ids: string[];
  decision_sequences: number[];
  children: EvidenceTreeNode[];
};

/* ---------------------------------------------------------- the resource */

export type Investigation = {
  id: string;
  status: InvestigationStatus;
  /** `RcaResult.state` - what the analysis found, not how the run went. */
  analysis_state: string | null;
  question: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
  /** What this run could not do. Populated when the status is `partial`. */
  limitations: string[];

  engine_version: string;
  max_drivers: number;
  max_tree_depth: number;
  grain: string | null;

  source: InvestigationSource;
  execution: Execution;
  evidence_quality: EvidenceQuality;
  reconciliation: Reconciliation;
  decisions: Decision[];
  notices: Array<Record<string, unknown>>;

  /**
   * The findings, in exactly the shape the stateless RCA endpoint returns, so
   * every existing component reads it unchanged. Null only on a failed run.
   */
  result: InvestigationResult | null;
  /** The evidence-linked tree. `result.rca_tree` has no evidence ids. */
  tree: EvidenceTreeNode | null;

  evidence_count: number;
  query_count: number;
  audit_event_count: number;
};

export type InvestigationSummary = {
  id: string;
  dataset_id: string;
  kpi_definition_id: string | null;
  status: InvestigationStatus;
  analysis_state: string | null;
  question: string | null;
  kpi_name: string;
  previous_value: number | null;
  current_value: number | null;
  absolute_change: number | null;
  percentage_change: number | null;
  change_direction: string | null;
  severity: string | null;
  evidence_quality: EvidenceQualityVerdict | null;
  reconciliation_status: ReconciliationStatus | null;
  evidence_count: number;
  created_at: string;
  completed_at: string | null;
};

export type InvestigationCreateRequest = {
  dataset_id: string;
  kpi_definition_id?: string | null;
  max_drivers?: number;
  max_tree_depth?: number;
  question?: string | null;
};
