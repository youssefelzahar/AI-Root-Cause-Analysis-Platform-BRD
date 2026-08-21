import Link from "next/link";

import { Alert, Panel, PanelHeading } from "@/components/ui";

/**
 * Reasons a detection cannot run that the user can act on.
 *
 * Handled here rather than in error.tsx because Next.js sanitises
 * server-component errors in production - the error code and message do not
 * survive the boundary, so a mapping there would work in dev and quietly stop
 * working once deployed. They are also not really failures: "no KPI configured
 * yet" is a step in the workflow.
 */
const BLOCKED: Record<
  string,
  { title: string; body: string; href?: string; label?: string }
> = {
  DATASET_NOT_ANALYSIS_READY: {
    title: "This dataset is not ready to analyse",
    body: "Configure a KPI so the engine knows which measure and time column to build a history from.",
    href: "kpi",
    label: "Configure a KPI",
  },
  KPI_DEFINITION_NOT_FOUND: {
    title: "No KPI is configured",
    body: "Anomaly detection judges a KPI against its own history, so one has to be defined first.",
    href: "kpi",
    label: "Configure a KPI",
  },
  KPI_TIME_COLUMN_REQUIRED: {
    title: "This KPI has no time column",
    body: "A KPI needs a date or timestamp column before it has a history to compare against. Add one in KPI setup.",
    href: "kpi",
    label: "Edit the KPI",
  },
  ANOMALY_COLUMN_MISSING: {
    title: "A column this KPI relies on is missing",
    body: "The dataset no longer has a column the KPI was built on - it may have been replaced with a file of a different shape. Reconfigure the KPI against the columns that exist now.",
    href: "kpi",
    label: "Edit the KPI",
  },
  ANOMALY_MEASURE_NOT_NUMERIC: {
    title: "The measure cannot be read as a number",
    body: "This KPI aggregates a column whose values do not convert to numbers, so there is no series to build.",
    href: "profile?tab=quality",
    label: "View the quality report",
  },
  ANOMALY_TIME_COLUMN_NOT_TEMPORAL: {
    title: "The time column cannot be read as a date",
    body: "The chosen time column holds values that do not convert to dates, so the periods cannot be worked out.",
    href: "profile?tab=quality",
    label: "View the quality report",
  },
  ANOMALY_GRAIN_UNSUPPORTED: {
    title: "That reporting grain cannot be used",
    body: "A series can be built by day, week, month, quarter or year.",
  },
  ANOMALY_METHOD_UNSUPPORTED: {
    title: "That detection method is not available",
    body: "This build ships a robust z-score detector and an IQR detector. A seasonal detector is not implemented.",
  },
  ANOMALY_AGGREGATION_UNSUPPORTED: {
    title: "That aggregation is not supported",
    body: "The KPI names an aggregation the engine does not recognise. Reconfigure it in KPI setup.",
    href: "kpi",
    label: "Edit the KPI",
  },
  DATA_NOT_READY: {
    title: "This dataset has no stored data",
    body: "The upload did not complete, so there is nothing to analyse.",
  },
};

export function isBlockedCode(code: string | undefined): boolean {
  return code !== undefined && code in BLOCKED;
}

export function AnomalyBlocked({
  code,
  datasetId,
  message,
}: {
  code: string;
  datasetId: string;
  message: string;
}) {
  const entry = BLOCKED[code];

  return (
    <Panel>
      <PanelHeading eyebrow="Not enough to go on" title={entry.title} />
      <p>{entry.body}</p>
      <Alert tone="info" title="What the API reported">
        <span className="mono">{code}</span> — {message}
      </Alert>
      <div className="form-actions">
        {entry.href ? (
          <Link className="btn" href={`/datasets/${datasetId}/${entry.href}`}>
            {entry.label}
          </Link>
        ) : null}
        <Link className="btn btn-secondary" href={`/datasets/${datasetId}`}>
          Dataset details
        </Link>
        <Link className="btn btn-ghost" href="/anomalies">
          Back to anomalies
        </Link>
      </div>
    </Panel>
  );
}
