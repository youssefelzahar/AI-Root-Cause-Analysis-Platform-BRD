import type { Aggregation, ComparisonPeriod } from "@/types/api";

export type AnalysisState =
  | "ok"
  | "no_data"
  | "no_previous_period"
  | "no_change"
  | "no_time_column"
  | "unattributable";

export type AttributionBasis =
  | "net_change"
  | "mix_rate"
  | "gross_movement"
  | "unattributable";

export type ChangePattern =
  | "single_driver"
  | "concentrated"
  | "broad_based"
  | "offsetting"
  | "none";

export type Classification =
  | "primary"
  | "secondary"
  | "offsetting"
  | "immaterial"
  | "residual";

export type Severity = "low" | "medium" | "high" | "critical";
export type ChangeDirection = "up" | "down" | "flat" | "unknown";

export type Period = {
  label: string;
  /** Inclusive. */
  start: string;
  /** Exclusive, so the previous window ends exactly where the current begins. */
  end: string;
  row_count: number;
};

export type Periods = {
  current: Period;
  previous: Period;
  grain: string;
  strategy: string;
  anchor: string;
  /** Set when the newest period was still being collected and was left out. */
  excluded_partial_period: Period | null;
};

export type KpiChange = {
  name: string;
  column: string;
  aggregation: Aggregation;
  time_column: string | null;
  current_value: number | null;
  previous_value: number | null;
  absolute_change: number | null;
  percent_change: number | null;
  percent_change_undefined_reason: string | null;
  direction: ChangeDirection;
  severity: Severity;
  comparison: ComparisonPeriod;
  grain: string;
};

export type Attribution = {
  basis: AttributionBasis;
  unattributable_reason: string | null;
  change_pattern: ChangePattern;
  pareto_target: number;
  min_material_contribution: number;
  has_offsetting: boolean;
  additivity_verified: boolean | null;
};

export type Driver = {
  node_id: string;
  depth: number;
  path: { dimension: string | null; value: string | null }[];
  dimension: string | null;
  value: string | null;
  value_is_null: boolean;

  current_value: number | null;
  previous_value: number | null;
  absolute_change: number | null;
  percent_change: number | null;
  percent_change_undefined_reason: string | null;

  /** Share of the whole KPI change, at every depth - not of the parent. */
  contribution: number | null;
  contribution_basis: AttributionBasis;
  unattributable_reason: string | null;
  share_of_parent_change: number | null;
  /** AVG only, and level-local: never sum these across depths. */
  rate_effect: number | null;
  mix_effect: number | null;

  current_count: number;
  previous_count: number;
  current_rows: number;
  previous_rows: number;
  current_share: number | null;
  previous_share: number | null;
  expected_change: number | null;
  excess_change: number | null;

  is_new_segment: boolean;
  is_lost_segment: boolean;
  low_support: boolean;
  support_reason: string | null;
  is_other_bucket: boolean;
  is_pure_split: boolean;

  classification: Classification;
  rank: number;
  child_dimension: string | null;
  child_split_type: string | null;
  child_explanatory_power: number | null;
  unexplained_share: number | null;
  stop_reason: string | null;
  children: Driver[];
};

export type DimensionResult = {
  dimension: string;
  segments: Driver[];
};

export type DimensionSummary = {
  dimension: string;
  segment_count: number;
  truncated: boolean;
  explanatory_power: number | null;
  excluded_reason: string | null;
};

export type Evidence = {
  total_rows: number;
  current_rows: number;
  previous_rows: number;
  rows_outside_periods: number;
  unparsed_time_rows: number;
  unparsed_measure_rows: number;
  statements_executed: number;
  duration_ms: number;
  contribution_sum: number | null;
};

export type Notice = {
  code: string;
  severity: "info" | "warning";
  message: string;
  details: Record<string, unknown> | null;
};

export type InvestigationResult = {
  dataset_id: string;
  dataset_name: string;
  kpi_definition_id: string;
  generated_at: string;
  state: AnalysisState;
  kpi: KpiChange;
  attribution: Attribution;
  periods: Periods | null;
  /** Depth-1 nodes only, so nothing is double counted against the tree. */
  primary_drivers: Driver[];
  secondary_drivers: Driver[];
  offsetting_factors: Driver[];
  dimension_results: DimensionResult[];
  dimensions_analysed: DimensionSummary[];
  rca_tree: Driver | null;
  evidence: Evidence;
  notices: Notice[];
  summary: string;
};
