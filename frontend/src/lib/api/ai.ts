import { cache } from "react";

import type { AiHealth, AiTool, AnalyzeRequest, AnalyzeResponse } from "@/types/ai";

import { apiFetch } from "./http";

/**
 * Ask a question about a dataset.
 *
 * Deliberately NOT wrapped in cache(): asking may create an investigation, so it
 * is a mutation. cache() would make two questions in one render silently return
 * the same answer - the same trap `createInvestigation` documents.
 *
 * The timeout matches `createInvestigation`'s, because the first question on a
 * dataset does what that endpoint does - scan the file - and then pays for two
 * model calls on top. A reused investigation returns in about a second.
 */
export const analyze = (payload: AnalyzeRequest) =>
  apiFetch<AnalyzeResponse>("/api/ai/analyze", {
    method: "POST",
    body: {
      dataset_id: payload.dataset_id,
      question: payload.question,
      kpi_definition_id: payload.kpi_definition_id ?? null,
      investigation_id: payload.investigation_id ?? null,
      refresh: payload.refresh ?? false,
    },
    timeoutMs: 120_000,
  });

/**
 * Whether the model is reachable.
 *
 * cache()d because it is a pure read called during a server render, and the page
 * uses it to decide whether to warn before anyone types a question. Always 200
 * with an `ok` flag, so a dead model daemon does not take the page down with it.
 */
export const getAiHealth = cache(() => apiFetch<AiHealth>("/api/ai/health"));

export const listAiTools = cache(() => apiFetch<AiTool[]>("/api/ai/tools"));
