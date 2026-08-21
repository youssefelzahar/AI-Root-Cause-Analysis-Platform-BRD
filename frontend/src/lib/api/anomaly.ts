import { cache } from "react";

import type { DetectionResult } from "@/types/anomaly";

import { apiFetch } from "./http";

/**
 * Detect anomalies in a dataset's active KPI over its own history.
 *
 * No try/catch and no fallback: a failed detection must surface as an ApiError
 * so the page can name the reason. Several failures are ordinary states a user
 * can act on - no KPI configured, no time column on the one that is - and
 * silently rendering an empty chart would hide them.
 *
 * cache() dedupes within a single server render.
 */
export const detectAnomalies = cache(
  (
    datasetId: string,
    options?: { kpiDefinitionId?: string; grain?: string; method?: string },
  ) =>
    apiFetch<DetectionResult>("/api/anomalies/detections", {
      method: "POST",
      body: {
        dataset_id: datasetId,
        kpi_definition_id: options?.kpiDefinitionId ?? null,
        grain: options?.grain ?? null,
        method: options?.method ?? "robust_zscore",
      },
      // The series is one scan of the file, but that scan can take a while.
      timeoutMs: 120_000,
    }),
);
