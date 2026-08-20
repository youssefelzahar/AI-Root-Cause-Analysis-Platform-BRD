import { cache } from "react";

import type { InvestigationResult } from "@/types/rca";

import { apiFetch } from "./http";

/**
 * Run a root cause analysis for a dataset's active KPI definition.
 *
 * No try/catch and no fallback: a failed investigation must surface as an
 * ApiError so error.tsx can name the reason. Several of the failures are
 * ordinary states a user can act on - no previous period to compare against, no
 * KPI configured yet - and silently rendering an empty result would hide them.
 *
 * cache() dedupes within a single server render.
 */
export const runInvestigation = cache(
  (datasetId: string, options?: { kpiDefinitionId?: string; maxTreeDepth?: number }) =>
    apiFetch<InvestigationResult>("/api/rca/investigations", {
      method: "POST",
      body: {
        dataset_id: datasetId,
        kpi_definition_id: options?.kpiDefinitionId ?? null,
        max_tree_depth: options?.maxTreeDepth ?? 3,
      },
      // A large dataset scans the file once, but that scan can take a while.
      timeoutMs: 120_000,
    }),
);

/**
 * Discard a dataset's investigation by deleting the KPI definition behind it.
 *
 * The dataset and its profile survive; only its Analysis Ready status goes, so
 * it leaves the investigations list until a new KPI is configured.
 */
export const deleteInvestigation = (datasetId: string) =>
  apiFetch<void>(`/api/rca/investigations/${datasetId}`, { method: "DELETE" });
