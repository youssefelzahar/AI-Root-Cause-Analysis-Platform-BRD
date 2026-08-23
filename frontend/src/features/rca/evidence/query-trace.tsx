"use client";

import { Code2 } from "lucide-react";
import { useState } from "react";

import { Alert, Pill, TableSkeleton } from "@/components/ui";
import { Dialog } from "@/components/ui/dialog";
import { listInvestigationQueries } from "@/lib/api/investigations";
import { toUserMessage } from "@/lib/api/errors";
import { formatDuration, formatNumber } from "@/lib/format";
import type { QueryTraceEntry } from "@/types/investigation";

/**
 * "Seven queries in 141 ms", made inspectable.
 *
 * One of only two client islands in the Evidence section. The SQL is the largest
 * part of the record and the least often wanted, so it is fetched on first open
 * and never on page load.
 *
 * Nothing here is ever synthesised. A statement with no recorded SQL says so.
 */

/** Why each statement ran, in the user's words. */
const PURPOSE_LABELS: Record<string, string> = {
  describe_relation: "Read the file's column types",
  project_base_table: "Project the measure, time and dimensions once",
  resolve_period_bounds: "Find the data's own date range",
  kpi_period_totals: "Total the KPI for both periods",
  dimension_breakdown: "Break every dimension down at once",
  distinct_overlap_check: "Check whether distinct counts are additive",
  drilldown_breakdown: "Break one segment down further",
  series_base_table: "Project the series for anomaly detection",
  series_bounds: "Find the series date range",
  series_aggregate: "Aggregate the KPI per period",
};

const STATUS_TONE: Record<string, "success" | "danger"> = {
  ok: "success",
  failed: "danger",
};

export function QueryTrace({
  investigationId,
  queryCount,
}: {
  investigationId: string;
  queryCount: number;
}) {
  const [open, setOpen] = useState(false);
  const [queries, setQueries] = useState<QueryTraceEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setQueries((await listInvestigationQueries(investigationId)).items);
    } catch (cause) {
      setError(toUserMessage(cause));
    } finally {
      setLoading(false);
    }
  };

  const openDialog = () => {
    setOpen(true);
    // Once only: the result persists across close and reopen.
    if (queries === null && !loading) void load();
  };

  return (
    <>
      <button
        type="button"
        className="btn btn-secondary btn-sm"
        disabled={queryCount === 0}
        onClick={openDialog}
      >
        <Code2 size={15} /> View queries ({formatNumber(queryCount)})
      </button>

      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        size="lg"
        title="Queries executed"
        description="Every statement this investigation ran, exactly as it was sent to DuckDB."
      >
        {loading ? <TableSkeleton rows={4} columns={4} /> : null}

        {error ? (
          <>
            <Alert tone="danger" title="Could not load the query trace">
              {error}
            </Alert>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => void load()}
            >
              Try again
            </button>
          </>
        ) : null}

        {queries !== null && queries.length === 0 ? (
          <p className="muted">The engine recorded no queries for this investigation.</p>
        ) : null}

        {queries !== null && queries.length > 0 ? (
          <ol className="query-trace">
            {queries.map((query) => (
              <li key={query.id} className="query-trace-item">
                <div className="query-trace-head">
                  <span className="query-trace-purpose">
                    {query.sequence}. {PURPOSE_LABELS[query.purpose] ?? query.purpose}
                  </span>
                  <Pill tone={STATUS_TONE[query.status] ?? "neutral"}>{query.status}</Pill>
                  <span className="query-trace-meta">
                    {formatDuration(query.duration_ms)} ·{" "}
                    {query.rows_returned === null
                      ? "no rows fetched"
                      : `${formatNumber(query.rows_returned)} rows`}
                    {query.parameter_count > 0
                      ? ` · ${query.parameter_count} bound value${
                          query.parameter_count === 1 ? "" : "s"
                        }`
                      : ""}
                    {query.node_id ? ` · ${query.node_id}` : ""}
                  </span>
                </div>
                {query.sql ? (
                  <pre className="query-trace-sql">{query.sql}</pre>
                ) : (
                  <p className="muted query-trace-sql">
                    SQL was not recorded for this statement.
                  </p>
                )}
                {query.error ? (
                  <p className="negative-text query-trace-meta">{query.error}</p>
                ) : null}
              </li>
            ))}
          </ol>
        ) : null}
      </Dialog>
    </>
  );
}
