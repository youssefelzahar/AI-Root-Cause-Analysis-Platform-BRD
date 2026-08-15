import type { Aggregation, KpiCandidates, KpiDefinitionPayload } from "@/types/api";

export const AGGREGATIONS: Aggregation[] = [
  "SUM",
  "AVG",
  "COUNT",
  "COUNT_DISTINCT",
  "MIN",
  "MAX",
  "MEDIAN",
];

export const COMPARISONS = [
  { value: "previous_period", label: "Previous period" },
  { value: "previous_month", label: "Previous month" },
  { value: "previous_quarter", label: "Previous quarter" },
  { value: "previous_year", label: "Previous year" },
] as const;

export const MAX_DIMENSIONS = 5;

/** Aggregations that need an actually-numeric column. */
const NUMERIC_AGGREGATIONS: Aggregation[] = ["SUM", "AVG", "MIN", "MAX", "MEDIAN"];

export type KpiFormState = {
  name: string;
  column: string;
  aggregation: Aggregation;
  time_column: string;
  dimensions: string[];
  comparison: KpiDefinitionPayload["comparison"];
};

/**
 * Mirrors the server-side rules so the user gets immediate feedback. The
 * backend re-validates; this never replaces it.
 */
export function validateKpiForm(
  state: KpiFormState,
  candidates: KpiCandidates | null,
): Record<string, string> {
  const errors: Record<string, string> = {};

  if (!state.name.trim()) {
    errors.name = "Give the KPI a name.";
  } else if (state.name.length > 150) {
    errors.name = "Keep the name under 150 characters.";
  }

  if (!state.column) {
    errors.column = "Choose the column to measure.";
  } else if (NUMERIC_AGGREGATIONS.includes(state.aggregation)) {
    const measure = candidates?.measures.find((item) => item.column === state.column);
    // Only flag columns we know are non-numeric; an unlisted column may still
    // be a valid manual override.
    const dimensionOnly = candidates?.dimensions.find((item) => item.column === state.column);
    if (!measure && dimensionOnly) {
      errors.column = `${state.aggregation} needs a numeric column. Use COUNT or COUNT_DISTINCT instead.`;
    }
  }

  if (state.dimensions.length === 0) {
    errors.dimensions = "Select at least one dimension to break the KPI down by.";
  } else if (state.dimensions.length > MAX_DIMENSIONS) {
    errors.dimensions = `Select at most ${MAX_DIMENSIONS} dimensions.`;
  }

  if (state.dimensions.includes(state.column)) {
    errors.dimensions = "The KPI column cannot also be a dimension.";
  }
  if (state.time_column && state.dimensions.includes(state.time_column)) {
    errors.dimensions = "The time column cannot also be a dimension.";
  }

  return errors;
}

export function toPayload(state: KpiFormState): KpiDefinitionPayload {
  return {
    name: state.name.trim(),
    column: state.column,
    aggregation: state.aggregation,
    time_column: state.time_column || null,
    dimensions: state.dimensions,
    comparison: state.comparison,
  };
}

export function describe(state: KpiFormState): string {
  const comparison =
    COMPARISONS.find((item) => item.value === state.comparison)?.label.toLowerCase() ??
    state.comparison;
  const dimensions = state.dimensions.length ? state.dimensions.join(", ") : "no dimensions";
  return `${state.aggregation} of ${state.column || "…"} over ${state.time_column || "…"}, compared to the ${comparison}, broken down by ${dimensions}.`;
}
