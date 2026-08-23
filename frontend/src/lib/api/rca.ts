import { apiFetch } from "./http";

/**
 * Discard a dataset's investigation by deleting the KPI definition behind it.
 *
 * The dataset and its profile survive; only its Analysis Ready status goes, so
 * it leaves the investigations list until a new KPI is configured.
 */
export const deleteInvestigation = (datasetId: string) =>
  apiFetch<void>(`/api/rca/investigations/${datasetId}`, { method: "DELETE" });
