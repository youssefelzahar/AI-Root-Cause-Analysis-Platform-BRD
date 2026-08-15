import { AlertTriangle } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";
import { Alert, Panel, PanelHeading, StatTile } from "@/components/ui";
import { DriverTable } from "@/features/rca/driver-table";
import { runDemoInvestigation } from "@/lib/api/rca";

export const dynamic = "force-dynamic";

export default async function InvestigationsPage() {
  // No fallback: if the backend is down this now fails visibly into error.tsx,
  // instead of rendering hardcoded data that looks real.
  const result = await runDemoInvestigation();
  const anomaly = result.anomaly;
  const tone = anomaly.absolute_change < 0 ? "negative" : "positive";

  return (
    <div>
      <PageHeader
        eyebrow="Active incident"
        title={`${anomaly.metric_name} anomaly investigation`}
        description="Demonstration data. Running RCA against your own datasets arrives in the next phase."
      />

      <Alert tone="info" title="Phase 1 scope">
        This dashboard runs on demonstration data. Datasets you mark{" "}
        <Link href="/datasets">Analysis Ready</Link> store a normalized KPI definition that the RCA
        engine will consume in the next phase.
      </Alert>

      <section className="alert alert-warning">
        <AlertTriangle size={24} />
        <div>
          <strong>{anomaly.severity.toUpperCase()} SEVERITY</strong>
          <p>{result.summary}</p>
        </div>
      </section>

      <section className="stats-grid">
        <StatTile label="Baseline avg" value={anomaly.baseline_average.toLocaleString()} />
        <StatTile label="Comparison avg" value={anomaly.comparison_average.toLocaleString()} />
        <StatTile
          label="Absolute change"
          value={anomaly.absolute_change.toLocaleString()}
          tone={tone}
        />
        <StatTile label="Percent change" value={`${anomaly.percent_change}%`} tone={tone} />
      </section>

      <div className="content-grid">
        <Panel>
          <PanelHeading eyebrow="Evidence" title="Top contributing drivers" />
          <DriverTable drivers={result.top_drivers} />
        </Panel>

        <Panel>
          <PanelHeading eyebrow="Recommended actions" title="Next steps" />
          <ul className="actions-list">
            {result.recommended_actions.map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
        </Panel>
      </div>
    </div>
  );
}
