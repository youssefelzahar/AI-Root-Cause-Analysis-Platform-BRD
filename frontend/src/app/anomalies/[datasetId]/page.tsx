import { AlertTriangle, BarChart3, LineChart, Table2 } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";
import { Alert, Panel, PanelHeading } from "@/components/ui";
import { AnomalyBlocked, isBlockedCode } from "@/features/anomaly/anomaly-blocked";
import { AnomalyChart } from "@/features/anomaly/anomaly-chart";
import { AnomalySummary } from "@/features/anomaly/anomaly-summary";
import { AnomalyTable } from "@/features/anomaly/anomaly-table";
import { InvestigationNotices } from "@/features/rca/investigation-notices";
import { detectAnomalies } from "@/lib/api/anomaly";
import { ApiError } from "@/lib/api/errors";
import type { AnomalySeverity, DetectionResult } from "@/types/anomaly";

export const dynamic = "force-dynamic";

const SEVERITY_TONE: Record<AnomalySeverity, "info" | "warning" | "danger" | "success"> = {
  NORMAL: "success",
  LOW: "info",
  MEDIUM: "warning",
  HIGH: "warning",
  CRITICAL: "danger",
};

/** Why a detection produced no verdicts, in the user's terms. */
const STATUS_EXPLANATION: Record<string, string> = {
  NO_DATA: "This dataset has no rows with a readable date, so there is no history to build.",
  INSUFFICIENT_HISTORY:
    "There are too few periods to judge any of them. A period is compared against the ones before it, so the start of every series is unscored.",
  NO_TIME_COLUMN:
    "This KPI has no time column, so it has no history to compare against.",
};

export default async function AnomalyPage({
  params,
}: {
  params: Promise<{ datasetId: string }>;
}) {
  const { datasetId } = await params;

  // Expected, actionable outcomes are rendered inline; anything else propagates
  // to error.tsx rather than being flattened into an empty-looking result.
  let result: DetectionResult;
  try {
    result = await detectAnomalies(datasetId);
  } catch (error) {
    if (error instanceof ApiError && isBlockedCode(error.code)) {
      return (
        <div>
          <PageHeader
            eyebrow="Anomaly detection"
            title="Cannot check this dataset yet"
            backHref="/anomalies"
            backLabel="Anomalies"
          />
          <AnomalyBlocked code={error.code} datasetId={datasetId} message={error.message} />
        </div>
      );
    }
    throw error;
  }

  const { kpi, method, latest, evidence } = result;
  const explanation = STATUS_EXPLANATION[result.status];
  const unscored = evidence.periods_observed - evidence.periods_evaluated;

  return (
    <div>
      <PageHeader
        eyebrow="Anomaly detection"
        title={`${kpi.name} — ${result.dataset_name}`}
        description={result.summary}
        backHref="/anomalies"
        backLabel="Anomalies"
        actions={
          <>
            <Link className="btn btn-secondary" href={`/datasets/${datasetId}/profile`}>
              <Table2 size={16} /> View profile
            </Link>
            <Link className="btn btn-secondary" href={`/datasets/${datasetId}/kpi`}>
              <BarChart3 size={16} /> KPI setup
            </Link>
            <Link className="btn btn-secondary" href={`/investigations/${datasetId}`}>
              <LineChart size={16} /> Explain the change
            </Link>
          </>
        }
      />

      {explanation ? (
        <Alert tone="info" title="Nothing to judge">
          {explanation}
        </Alert>
      ) : null}

      {latest?.is_anomaly ? (
        <section className={`alert alert-${SEVERITY_TONE[latest.severity]}`}>
          <AlertTriangle size={24} />
          <div>
            <strong>{latest.severity} ANOMALY</strong>
            <p>{result.summary}</p>
          </div>
        </section>
      ) : null}

      {latest ? (
        <AnomalySummary latest={latest} method={method} kpi={kpi} evidence={evidence} />
      ) : null}

      {result.notices.length > 0 ? (
        <Panel>
          <PanelHeading eyebrow="How this was measured" title="Notes on this analysis" />
          <InvestigationNotices notices={result.notices} />
        </Panel>
      ) : null}

      {result.series.length > 0 ? (
        <Panel>
          <PanelHeading
            eyebrow="History"
            title={`${kpi.name} by ${kpi.grain}, against its baseline`}
          />
          <AnomalyChart
            series={result.series}
            threshold={method.anomaly_threshold}
            kpiName={kpi.name}
          />
          {unscored > 0 ? (
            <p className="muted">
              {`${unscored} of ${evidence.periods_observed} periods could not be judged - the `}
              {`baseline needs at least ${method.min_baseline_observations} earlier periods, so the `}
              {`start of the series is never scored. They are drawn hollow, not as normal periods.`}
            </p>
          ) : null}
        </Panel>
      ) : null}

      <Panel>
        <PanelHeading
          eyebrow="Flagged periods"
          title="Periods that departed from the baseline"
        />
        <AnomalyTable
          observations={result.anomalies}
          emptyMessage="No period departed from its baseline by more than the threshold."
        />
      </Panel>

      <Panel>
        <PanelHeading eyebrow="Limits" title="What this method cannot see" />
        <ul className="issue-list">
          {result.limitations.map((text) => (
            <li key={text} className="issue info">
              {text}
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
