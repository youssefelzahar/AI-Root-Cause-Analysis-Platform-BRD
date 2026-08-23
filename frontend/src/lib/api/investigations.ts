import { cache } from "react";

import type { Page } from "@/types/api";
import type {
  AuditEvent,
  EvidenceRecord,
  Investigation,
  InvestigationCreateRequest,
  InvestigationSummary,
  QueryTraceEntry,
} from "@/types/investigation";

import { apiFetch } from "./http";

/**
 * Create and persist an investigation.
 *
 * Deliberately NOT wrapped in cache(): this is a mutation. cache() would make
 * two calls in one render silently return the same row, which is exactly the
 * bug it looks like it is preventing. The old runInvestigation was cached only
 * because it created nothing.
 *
 * The backend returns 201 for a new run and 200 when it reused an equivalent
 * completed investigation of unchanged data. Both carry the same body, so the
 * caller does not have to care.
 */
export const createInvestigation = (
  payload: InvestigationCreateRequest,
  options?: { refresh?: boolean },
) =>
  apiFetch<Investigation>(
    `/api/investigations${options?.refresh ? "?refresh=true" : ""}`,
    {
      method: "POST",
      body: {
        dataset_id: payload.dataset_id,
        kpi_definition_id: payload.kpi_definition_id ?? null,
        max_drivers: payload.max_drivers ?? 5,
        max_tree_depth: payload.max_tree_depth ?? 3,
        question: payload.question ?? null,
      },
      // The first run scans the file; a reuse returns immediately.
      timeoutMs: 120_000,
    },
  );

/** cache() dedupes within a single server render. */
export const getInvestigation = cache((id: string) =>
  apiFetch<Investigation>(`/api/investigations/${id}`),
);

export const listInvestigationEvidence = cache((id: string) =>
  apiFetch<Page<EvidenceRecord>>(`/api/investigations/${id}/evidence?limit=200`),
);

export const listInvestigations = cache(
  (params?: { datasetId?: string; status?: string; limit?: number }) => {
    const query = new URLSearchParams();
    if (params?.datasetId) query.set("dataset_id", params.datasetId);
    if (params?.status) query.set("status", params.status);
    query.set("limit", String(params?.limit ?? 50));
    return apiFetch<Page<InvestigationSummary>>(`/api/investigations?${query}`);
  },
);

/**
 * Browser-called, so no cache(): it is a per-render dedupe and does nothing
 * here. The components that call these fetch once on first open and keep the
 * result, which is the dedupe that actually matters.
 */
export const listInvestigationQueries = (id: string) =>
  apiFetch<Page<QueryTraceEntry>>(`/api/investigations/${id}/queries?limit=200`);

export const listInvestigationAudit = (id: string) =>
  apiFetch<Page<AuditEvent>>(`/api/investigations/${id}/audit?limit=200`);

export const getEvidence = (id: string) =>
  apiFetch<EvidenceRecord>(`/api/evidence/${id}`);
