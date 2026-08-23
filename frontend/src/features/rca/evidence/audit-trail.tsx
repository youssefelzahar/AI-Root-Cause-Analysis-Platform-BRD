"use client";

import { type SyntheticEvent, useState } from "react";

import { Alert, TableSkeleton } from "@/components/ui";
import { toUserMessage } from "@/lib/api/errors";
import { listInvestigationAudit } from "@/lib/api/investigations";
import { formatDate, formatDuration, formatNumber } from "@/lib/format";
import type { AuditEvent } from "@/types/investigation";

/**
 * What happened during the run, in order.
 *
 * A lazy disclosure rather than a dialog: this is a dozen short lines, and a
 * modal for twelve timestamps is over-engineering. Unlike the evidence records
 * it is never an anchor target, so keeping it closed by default costs nothing.
 *
 * Client-side only for the fetch. It fires on the first open and never on page
 * load.
 */

/** The lifecycle events, including the unhappy-path counterparts. */
const EVENT_LABELS: Record<string, string> = {
  investigation_started: "Investigation started",
  periods_resolved: "Periods resolved",
  kpi_calculated: "KPI calculated",
  dimension_analysis_executed: "Dimension analysis executed",
  contributor_selected: "Contributor selected",
  drilldown_executed: "Drill-down executed",
  drilldown_stopped: "Drill-down stopped",
  anomaly_detection_executed: "Anomaly detection executed",
  anomaly_detection_skipped: "Anomaly detection skipped",
  evidence_built: "Evidence built",
  evidence_validated: "Evidence validated",
  reconciliation_passed: "Reconciliation passed",
  reconciliation_failed: "Reconciliation failed",
  investigation_completed: "Investigation completed",
  investigation_partial: "Investigation partial",
  investigation_failed: "Investigation failed",
};

export function AuditTrail({
  investigationId,
  eventCount,
}: {
  investigationId: string;
  eventCount: number;
}) {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (eventCount === 0) return null;

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setEvents((await listInvestigationAudit(investigationId)).items);
    } catch (cause) {
      setError(toUserMessage(cause));
    } finally {
      setLoading(false);
    }
  };

  const onToggle = (event: SyntheticEvent<HTMLDetailsElement>) => {
    if (!event.currentTarget.open || events !== null || loading) return;
    void load();
  };

  return (
    <details className="evidence-block" onToggle={onToggle}>
      <summary className="evidence-block-title">
        Audit trail ({formatNumber(eventCount)} events)
      </summary>

      {loading ? <TableSkeleton rows={4} columns={2} /> : null}

      {error ? (
        <>
          <Alert tone="danger" title="Could not load the audit trail">
            {error}
          </Alert>
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => void load()}>
            Try again
          </button>
        </>
      ) : null}

      {events !== null && events.length === 0 ? (
        <p className="muted">No audit events were recorded for this investigation.</p>
      ) : null}

      {events !== null && events.length > 0 ? (
        <ol className="audit-trail">
          {events.map((event) => (
            <li key={event.id} className="audit-event">
              <time className="audit-at" dateTime={event.occurred_at}>
                {formatDate(event.occurred_at)}
              </time>
              <span>
                <strong>{EVENT_LABELS[event.event_type] ?? event.event_type.replace(/_/g, " ")}</strong>
                {event.message ? <span className="muted"> — {event.message}</span> : null}
              </span>
              {/* The offset from the run's start, which is the reproducible
                  field: the wall-clock time is derived from it. */}
              <span className="audit-ms">{formatDuration(event.elapsed_ms)}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </details>
  );
}
