import { cache } from "react";

import type {
  DatasetDetail,
  DatasetStatusRead,
  DatasetSummary,
  KpiCandidates,
  KpiDefinition,
  KpiDefinitionEnvelope,
  KpiDefinitionPayload,
  Page,
  PreviewRead,
  ProfileEnvelope,
  SchemaValidation,
} from "@/types/api";

import { ApiError } from "./errors";
import { apiFetch } from "./http";

// cache() dedupes within a single server render, so a layout and its page can
// each ask for the dataset without issuing two requests.
export const listDatasets = cache(
  (params: { limit?: number; offset?: number; status?: string; search?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.limit) query.set("limit", String(params.limit));
    if (params.offset) query.set("offset", String(params.offset));
    if (params.status) query.set("status", params.status);
    if (params.search) query.set("search", params.search);
    const suffix = query.toString() ? `?${query}` : "";
    return apiFetch<Page<DatasetSummary>>(`/api/datasets${suffix}`);
  },
);

export const getDataset = cache((id: string) => apiFetch<DatasetDetail>(`/api/datasets/${id}`));

export const getDatasetStatus = (id: string) =>
  apiFetch<DatasetStatusRead>(`/api/datasets/${id}/status`);

export const getProfile = cache((id: string) =>
  apiFetch<ProfileEnvelope>(`/api/datasets/${id}/profile`),
);

export const getPreview = (id: string, limit = 25) =>
  apiFetch<PreviewRead>(`/api/datasets/${id}/preview?limit=${limit}`);

/**
 * Validation has not run until profiling finishes, and a 404 there is an
 * expected state rather than an error.
 */
export const getValidation = cache(async (id: string): Promise<SchemaValidation | null> => {
  try {
    return await apiFetch<SchemaValidation>(`/api/datasets/${id}/validation`);
  } catch (error) {
    if (error instanceof ApiError && error.isNotFound) return null;
    throw error;
  }
});

export const getKpiCandidates = cache(async (id: string): Promise<KpiCandidates | null> => {
  try {
    return await apiFetch<KpiCandidates>(`/api/datasets/${id}/kpi-candidates`);
  } catch (error) {
    // 409 while the profile is still being generated.
    if (error instanceof ApiError && (error.isNotReady || error.isNotFound)) return null;
    throw error;
  }
});

export const getActiveKpiDefinition = cache(async (id: string): Promise<KpiDefinition | null> => {
  try {
    return await apiFetch<KpiDefinition>(`/api/datasets/${id}/kpi-definitions/active`);
  } catch (error) {
    if (error instanceof ApiError && error.isNotFound) return null;
    throw error;
  }
});

export const createKpiDefinition = (id: string, payload: KpiDefinitionPayload) =>
  apiFetch<KpiDefinitionEnvelope>(`/api/datasets/${id}/kpi-definitions`, {
    method: "POST",
    body: payload,
  });

export const deleteDataset = (id: string) =>
  apiFetch<void>(`/api/datasets/${id}`, { method: "DELETE" });

export const regenerateProfile = (id: string) =>
  apiFetch<{ dataset_id: string; status: string }>(`/api/datasets/${id}/profile/regenerate`, {
    method: "POST",
  });
