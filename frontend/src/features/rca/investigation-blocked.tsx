import Link from "next/link";

import { Alert, Panel, PanelHeading } from "@/components/ui";

/**
 * Reasons an investigation cannot run that the user can act on.
 *
 * These are handled here rather than in error.tsx because Next.js sanitises
 * server-component errors in production - the error code and message do not
 * survive the boundary, so a mapping there would work in dev and quietly stop
 * working once deployed. They are also not really failures: "no KPI configured
 * yet" is a step in the workflow.
 */
const BLOCKED: Record<
  string,
  { title: string; body: string; href?: string; label?: string }
> = {
  RCA_NO_PREVIOUS_PERIOD: {
    title: "Only one period of data",
    body: "There is nothing before the current period to compare against. Root cause analysis explains a change over time, so it needs at least two periods.",
  },
  DATASET_NOT_ANALYSIS_READY: {
    title: "This dataset is not ready to analyse",
    body: "Configure a KPI so the engine knows which measure, time column and dimensions to explain.",
    href: "kpi",
    label: "Configure a KPI",
  },
  KPI_DEFINITION_NOT_FOUND: {
    title: "No KPI is configured",
    body: "Root cause analysis explains the change in a KPI, so one has to be defined first.",
    href: "kpi",
    label: "Configure a KPI",
  },
  KPI_TIME_COLUMN_REQUIRED: {
    title: "This KPI has no time column",
    body: "A period-over-period comparison needs a date or timestamp column. Add one in KPI setup.",
    href: "kpi",
    label: "Edit the KPI",
  },
  RCA_COLUMN_MISSING: {
    title: "A column this KPI relies on is missing",
    body: "The dataset no longer has a column the KPI was built on - it may have been replaced with a file of a different shape. Reconfigure the KPI against the columns that exist now.",
    href: "kpi",
    label: "Edit the KPI",
  },
  RCA_MEASURE_NOT_NUMERIC: {
    title: "The measure cannot be read as a number",
    body: "This KPI aggregates a column whose values do not convert to numbers, so there is no total to explain.",
    href: "profile?tab=quality",
    label: "View the quality report",
  },
  RCA_TIME_COLUMN_NOT_TEMPORAL: {
    title: "The time column cannot be read as a date",
    body: "The chosen time column holds values that do not convert to dates, so the periods cannot be worked out.",
    href: "profile?tab=quality",
    label: "View the quality report",
  },
  DATA_NOT_READY: {
    title: "This dataset has no stored data",
    body: "The upload did not complete, so there is nothing to analyse.",
  },
  // Investigations are scoped to the company that ran them. Without this the
  // frontend has no auth handling at all, so a 403 would land in error.tsx as a
  // sanitised message with no explanation.
  INVESTIGATION_FORBIDDEN: {
    title: "This investigation belongs to another workspace",
    body: "Investigations are scoped to the company that ran them, so this one cannot be opened from here.",
  },
  KPI_DEFINITION_CHANGED: {
    title: "The KPI behind this investigation has changed",
    body: "This snapshot was built from a KPI definition that has since been edited or removed, so it cannot be re-run as it was. Configure the KPI again and run a fresh investigation.",
    href: "kpi",
    label: "Edit the KPI",
  },
};

export function isBlockedCode(code: string | undefined): boolean {
  return code !== undefined && code in BLOCKED;
}

export function InvestigationBlocked({
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
        <Link className="btn btn-ghost" href="/investigations">
          Back to investigations
        </Link>
      </div>
    </Panel>
  );
}
