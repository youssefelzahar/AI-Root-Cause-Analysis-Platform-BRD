import { cache } from "react";

import type {
  DatasetDetail,
  Page,
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
 */
export type SqlConnectionInput = {
  name: string;
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  encrypt?: boolean;
  trust_server_certificate?: boolean;
};

export const listConnections = cache(() =>
  apiFetch<Page<SqlConnection>>("/api/sql-connections?limit=100"),
);

export const getConnection = cache((id: string) =>
  apiFetch<SqlConnection>(`/api/sql-connections/${id}`),
);

export const createConnection = (input: SqlConnectionInput) =>
  apiFetch<SqlConnection>("/api/sql-connections", { method: "POST", body: input });

export const testUnsavedConnection = (input: SqlConnectionInput) =>
  apiFetch<SqlConnectionTestResult>("/api/sql-connections/test", { method: "POST", body: input });

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
