import { cache } from "react";

import type {
  DatasetDetail,
  Page,
  SqlAuthMode,
  SqlConnection,
  SqlConnectionTestResult,
  SqlExecuteResult,
  SqlGuardResult,
} from "@/types/api";

import { apiFetch } from "./http";

/**
 * Credentials supplied by the connection form.
 *
 * The password is sent in a POST body once and never persisted client-side:
 * not in localStorage, not in a URL, not in any store.
 *
 * Under `windows` auth there is no password and no username to send at all - the
 * server authenticates as its own process identity. The backend rejects either
 * field in that mode rather than ignoring it, so `toRequest` strips them.
 */
export type SqlConnectionInput = {
  name: string;
  host: string;
  port: number;
  database: string;
  auth_mode: SqlAuthMode;
  username: string;
  password: string;
  encrypt?: boolean;
  trust_server_certificate?: boolean;
};

/**
 * The form state as the API expects it.
 *
 * Windows auth sends neither credential field. Sending an empty string would be
 * a 422 - the backend refuses a credential it will not use rather than dropping
 * it silently - and the form keeps whatever was typed before the mode switch, so
 * this cannot rely on the fields being blank.
 */
function toRequest(input: SqlConnectionInput): Record<string, unknown> {
  const { username, password, ...rest } = input;
  if (input.auth_mode === "windows") return rest;
  return { ...rest, username, password };
}

export const listConnections = cache(() =>
  apiFetch<Page<SqlConnection>>("/api/sql-connections?limit=100"),
);

export const getConnection = cache((id: string) =>
  apiFetch<SqlConnection>(`/api/sql-connections/${id}`),
);

export const createConnection = (input: SqlConnectionInput) =>
  apiFetch<SqlConnection>("/api/sql-connections", {
    method: "POST",
    body: toRequest(input),
  });

export const testUnsavedConnection = (input: SqlConnectionInput) =>
  apiFetch<SqlConnectionTestResult>("/api/sql-connections/test", {
    method: "POST",
    body: toRequest(input),
  });

export const testConnection = (id: string) =>
  apiFetch<SqlConnectionTestResult>(`/api/sql-connections/${id}/test`, { method: "POST" });

export const deleteConnection = (id: string) =>
  apiFetch<void>(`/api/sql-connections/${id}`, { method: "DELETE" });

/** Lint a statement without running it. */
export const validateSql = (sql: string) =>
  apiFetch<SqlGuardResult>("/api/sql/validate", { method: "POST", body: { sql } });

export const executeQuery = (connectionId: string, sql: string, rowLimit?: number) =>
  apiFetch<SqlExecuteResult>(`/api/sql/connections/${connectionId}/execute`, {
    method: "POST",
    body: { sql, row_limit: rowLimit },
  });

export const saveQueryAsDataset = (
  connectionId: string,
  input: { sql: string; dataset_name: string; description?: string },
) =>
  apiFetch<DatasetDetail>(`/api/sql/connections/${connectionId}/save-as-dataset`, {
    method: "POST",
    body: input,
  });
