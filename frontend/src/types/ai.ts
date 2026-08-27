/**
 * Mirrors `backend/app/schemas/ai.py` model for model, snake_case throughout, with
 * no mapping layer - the same rule the other type modules follow, and what makes
 * `npm run types:api` a usable drift check.
 *
 * The shape to notice: `answer` is prose and everything else is structured. A
 * number worth acting on has its own field, so nothing here is ever recovered by
 * parsing the paragraph. That is deliberate - the evidence layer removed exactly
 * that fragility from the rest of the app.
 */

/* ---- lifecycle */

/**
 * `clarification` is not a failure: the question was understood well enough to
 * know it cannot be answered as asked. `partial` means the analysis succeeded and
 * something optional did not - most often the written explanation.
 */
export type AnalystStatus = "completed" | "clarification" | "partial";

export type IntentKind =
  | "KPI_ANALYSIS"
  | "ROOT_CAUSE_ANALYSIS"
  | "ANOMALY_ANALYSIS"
  | "DIMENSION_ANALYSIS"
  | "CONTRIBUTION_ANALYSIS"
  | "DRILL_DOWN"
  | "INVESTIGATION_SUMMARY"
  | "FOLLOW_UP_ANALYSIS";

/* ---- the answer */

export type Clarification = {
  code: string;
  message: string;
  options: string[];
};

export type AnalystDriver = {
  dimension: string;
  value: string;
  absolute_change: number | null;
  /** Percentage points of the movement, already multiplied. */
  contribution_percentage: number | null;
  classification: string;
  rank: number;
  is_new_segment: boolean;
  is_lost_segment: boolean;
  /** The record behind this driver, for a link into the evidence panel. */
  evidence_id: string | null;
};

/** One executed analysis step, for the progress list. */
export type AnalystStep = {
  tool: string;
  ok: boolean;
  duration_ms: number;
  detail: string;
};

export type AnalystFact = {
  label: string;
  value: number | null;
  formatted: string;
};

/**
 * The grounded numbers, exactly as the answer was allowed to see them. Rendered
 * as the audit surface for the prose: a reader who doubts a sentence can compare
 * it against this without another request.
 */
export type AnalystEvidence = {
  kpi_name: string;
  previous_period: string | null;
  current_period: string | null;
  previous_value: number | null;
  current_value: number | null;
  absolute_change: number | null;
  percentage_change: number | null;
  direction: string | null;
  severity: string | null;
  attribution_basis: string | null;
  change_pattern: string | null;
  drill_path: string[];
  drill_stop_reason: string | null;
  anomaly_summary: string | null;
  evidence_quality: string | null;
  reconciliation_status: string | null;
  facts: AnalystFact[];
};

export type AnalyzeResponse = {
  status: AnalystStatus;
  question: string;
  intent: IntentKind | null;
  answer: string | null;
  /**
   * True when the prose was assembled from the evidence rather than generated -
   * the model was unavailable, or what it wrote quoted a figure the analysis never
   * produced. Surfaced so a reader is never shown one as the other.
   */
  answer_is_template: boolean;
  clarification: Clarification | null;
  investigation_id: string | null;
  drivers: AnalystDriver[];
  offsetting_factors: AnalystDriver[];
  evidence_ids: string[];
  evidence: AnalystEvidence | null;
  steps: AnalystStep[];
  /** What was assumed on the reader's behalf, stated rather than applied silently. */
  assumptions: string[];
  limitations: string[];
  model: string | null;
  rules_version: string;
  duration_ms: number;
};

export type AnalyzeRequest = {
  question: string;
  dataset_id: string;
  kpi_definition_id?: string | null;
  /** Continues an existing investigation instead of running a new one. */
  investigation_id?: string | null;
  refresh?: boolean;
};

/* ---- supporting endpoints */

export type AiHealth = {
  ok: boolean;
  enabled: boolean;
  provider: string;
  model: string | null;
  message: string;
  error_code: string | null;
  latency_ms: number;
};

export type AiTool = {
  name: string;
  description: string;
  arguments: Array<{ name: string; required: boolean }>;
};

/* ---- conversation state, client-side only */

/**
 * One exchange in the thread. Held in component state and nowhere else: §24 asks
 * for short-term context only, and the durable artifact is the investigation the
 * answer links to.
 */
export type AnalystTurn = {
  id: string;
  question: string;
  response: AnalyzeResponse | null;
  error: string | null;
};
