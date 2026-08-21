/**
 * Anomaly detection response types.
 *
 * Mirrors the backend Pydantic schemas in `app/schemas/anomaly.py` - snake_case
 * throughout, no camelCase mapping layer, string-literal unions rather than
 * enums, and `T | null` for anything the API can omit.
 */

import type { Aggregation } from "./api";

export type AnomalySeverity = "NORMAL" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export type AnomalyDirection = "UPWARD" | "DOWNWARD" | "NONE";

/**
 * Why a period does or does not carry a score.
 *
 * `INSUFFICIENT_HISTORY` and `MISSING` and `PARTIAL` are all "not judged", and
 * none of them means "normal" - the UI must render them distinctly or it will
 * claim clean periods it has no evidence for.
 */
export type ObservationStatus =
  | "EVALUATED"
  | "INSUFFICIENT_HISTORY"
  | "MISSING"
  | "PARTIAL";

export type DetectionStatus =
  | "OK"
  | "NO_DATA"
  | "INSUFFICIENT_HISTORY"
  | "NO_TIME_COLUMN";

/** Which dispersion estimate the score was divided by. */
export type ScaleBasis =
  | "mad"
  | "mean_absolute_deviation"
  | "iqr"
  | "degenerate";

export type AnomalyBaseline = {
  expected_value: number;
  scale: number;
  scale_basis: ScaleBasis;
  observations_used: number;
};

export type AnomalyObservation = {
  period_start: string;
  /** Exclusive, so one period ends exactly where the next begins. */
  period_end: string;
  /** Null only when the period has no rows at all - which is not a KPI of zero. */
  value: number | null;
  row_count: number;
  status: ObservationStatus;
  baseline: AnomalyBaseline | null;
  absolute_deviation: number | null;
  percentage_deviation: number | null;
  /** "zero_baseline" when there is no percentage to give. */
  percentage_unavailable_reason: string | null;
  anomaly_score: number | null;
  severity: AnomalySeverity;
  direction: AnomalyDirection;
  is_anomaly: boolean;
};

export type AnomalyMethod = {
  name: string;
  baseline_window: number;
  min_baseline_observations: number;
  anomaly_threshold: number;
  severity_thresholds: Record<string, number>;
  score_interpretation: string;
};

export type AnomalyEvidence = {
  total_rows: number;
  rows_in_series: number;
  unparsed_time_rows: number;
  unparsed_measure_rows: number;
  periods_observed: number;
  periods_missing: number;
  periods_evaluated: number;
  statements_executed: number;
  duration_ms: number;
};

export type AnomalyNotice = {
  code: string;
  severity: "info" | "warning";
  message: string;
  details: Record<string, unknown> | null;
};

export type AnomalyKpi = {
  name: string;
  column: string;
  aggregation: Aggregation;
  time_column: string | null;
  grain: string;
};

export type DetectionResult = {
  dataset_id: string;
  dataset_name: string;
  kpi_definition_id: string;
  generated_at: string;
  status: DetectionStatus;
  kpi: AnomalyKpi;
  method: AnomalyMethod;
  /** Every period on the calendar, gaps included. This is what the chart draws. */
  series: AnomalyObservation[];
  anomalies: AnomalyObservation[];
  /** The most recent evaluated period, or null if none could be judged. */
  latest: AnomalyObservation | null;
  evidence: AnomalyEvidence;
  notices: AnomalyNotice[];
  limitations: string[];
  summary: string;
};
