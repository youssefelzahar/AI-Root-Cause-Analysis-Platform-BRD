import { AlertTriangle, BarChart3, Table2 } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";
import { Alert, Panel, PanelHeading } from "@/components/ui";
import { DriverTable } from "@/features/rca/driver-table";
import { EvidencePanel } from "@/features/rca/evidence-panel";
import {
  InvestigationBlocked,
  isBlockedCode,
} from "@/features/rca/investigation-blocked";
import { DeleteInvestigationButton } from "@/features/rca/delete-investigation-button";
import { InvestigationNotices } from "@/features/rca/investigation-notices";
import { KpiSummary } from "@/features/rca/kpi-summary";
import { RcaTree } from "@/features/rca/rca-tree";
import { Waterfall } from "@/features/rca/waterfall";
import { ApiError } from "@/lib/api/errors";
import { runInvestigation } from "@/lib/api/rca";
import type { InvestigationResult, Severity } from "@/types/rca";

export const dynamic = "force-dynamic";

const SEVERITY_TONE: Record<Severity, "info" | "warning" | "danger" | "success"> = {
  low: "info",
  medium: "info",
  high: "warning",
  critical: "danger",
};

/** Why an investigation produced no drivers, in the user's terms. */
const STATE_EXPLANATION: Record<string, string> = {
  no_data: "This dataset has no rows to analyse.",
  no_previous_period:
    "There is only one period of data, so there is nothing to compare the current one against.",
  no_change: "The KPI is unchanged between the two periods, so there is nothing to explain.",
  no_time_column:
    "This KPI has no time column, so a period-over-period comparison is not possible.",
};

export default async function InvestigationPage({
  params,
}: {
  params: Promise<{ datasetId: string }>;
}) {
  const { datasetId } = await params;

  // Expected, actionable outcomes are rendered inline; anything else propagates
  // to error.tsx rather than being flattened into an empty-looking result.
  let result: InvestigationResult;
  try {
    result = await runInvestigation(datasetId);
  } catch (error) {
    if (error instanceof ApiError && isBlockedCode(error.code)) {
      return (
        <div>
          <PageHeader
            eyebrow="Root cause analysis"
            title="Cannot analyse this dataset yet"
            backHref="/investigations"
            backLabel="Investigations"
          />
          <InvestigationBlocked
            code={error.code}
            datasetId={datasetId}
            message={error.message}
          />
        </div>
      );
    }
    throw error;
  }

  const { kpi, attribution, periods } = result;

  const attributable = attribution.basis !== "unattributable";
  const showEffects = attribution.basis === "mix_rate";
  const explanation = STATE_EXPLANATION[result.state];

  return (
    <div>
      <PageHeader
        eyebrow="Root cause analysis"
        title={`${kpi.name} — ${result.dataset_name}`}
        description={result.summary}
        backHref="/investigations"
        backLabel="Investigations"
        actions={
          <>
            <Link className="btn btn-secondary" href={`/datasets/${datasetId}/profile`}>
              <Table2 size={16} /> View profile
            </Link>
            <Link className="btn btn-secondary" href={`/datasets/${datasetId}/kpi`}>
              <BarChart3 size={16} /> KPI setup
            </Link>
            {/* Back to the list: without its KPI this dataset has no page here. */}
            <DeleteInvestigationButton
              datasetId={datasetId}
              iconOnly={false}
              name={result.dataset_name}
              redirectTo="/investigations"
            />
          </>
        }
      />

      {explanation ? <Alert tone="info" title="Nothing to attribute">{explanation}</Alert> : null}

      {result.state === "ok" && kpi.absolute_change !== null ? (
        <section className={`alert alert-${SEVERITY_TONE[kpi.severity]}`}>
          <AlertTriangle size={24} />
          <div>
            <strong>{kpi.severity.toUpperCase()} SEVERITY</strong>
            <p>{result.summary}</p>
          </div>
        </section>
      ) : null}

      {attribution.change_pattern === "broad_based" ? (
        <Alert tone="info" title="The change is broad-based">
          Every segment moved roughly in proportion to its size, so no single segment stands out as a
          driver. Naming one would be misleading.
        </Alert>
      ) : null}

      {attribution.basis === "gross_movement" ? (
        <Alert tone="warning" title="Large movements cancelled out">
          The net change is very small compared with how much individual segments moved, so shares
          below are of total movement rather than of the net change.
        </Alert>
      ) : null}

      {!attributable ? (
        <Alert tone="info" title={`${kpi.aggregation} cannot be split across segments`}>
          A {kpi.aggregation.toLowerCase()} of the whole dataset is not the sum of the
          {" "}{kpi.aggregation.toLowerCase()}s of its parts, so there is no mathematically valid way
          to say what share each segment contributed. Per-segment values and changes are shown below
          without contribution percentages.
        </Alert>
      ) : null}

      <KpiSummary kpi={kpi} periods={periods} />

      {result.notices.length > 0 ? (
        <Panel>
          <PanelHeading eyebrow="How this was measured" title="Notes on this analysis" />
          <InvestigationNotices notices={result.notices} />
        </Panel>
      ) : null}

      {attributable && result.primary_drivers.length > 0 ? (
        <Panel>
          <PanelHeading
            eyebrow="Contribution"
            title={`From ${kpi.name} previous to current`}
          />
          <Waterfall
            kpi={kpi}
            drivers={[...result.primary_drivers, ...result.secondary_drivers]}
          />
        </Panel>
      ) : null}

      <div className="content-grid">
        <Panel>
          <PanelHeading
            eyebrow="Primary drivers"
            title={`Segments accounting for most of the change`}
          />
          <DriverTable
            drivers={result.primary_drivers}
            showEffects={showEffects}
            emptyMessage={
              attributable
                ? "No single segment accounts for a material share of the change."
                : "Contributions cannot be attributed for this aggregation."
            }
          />
        </Panel>

        <Panel>
          <PanelHeading
            eyebrow="Offsetting factors"
            title="Segments that pushed the other way"
          />
          <DriverTable
            drivers={result.offsetting_factors}
            showEffects={showEffects}
            emptyMessage="Every segment moved in the same direction as the KPI."
          />
        </Panel>
      </div>

      {result.rca_tree ? (
        <Panel>
          <PanelHeading eyebrow="Hierarchy" title="Where the change concentrates" />
          <RcaTree root={result.rca_tree} kpiName={kpi.name} />
        </Panel>
      ) : null}

      {result.secondary_drivers.length > 0 ? (
        <Panel>
          <PanelHeading eyebrow="Secondary drivers" title="Smaller contributors" />
          <DriverTable
            drivers={result.secondary_drivers}
            showEffects={showEffects}
            emptyMessage="None."
          />
        </Panel>
      ) : null}

      {!attributable && result.dimension_results.length > 0 ? (
        <Panel>
          <PanelHeading eyebrow="Segment detail" title="How each segment moved" />
          {result.dimension_results.map((entry) => (
            <div key={entry.dimension} className="rca-dimension-table">
              <PanelHeading title={entry.dimension} />
              <DriverTable drivers={entry.segments} emptyMessage="No segments." />
            </div>
          ))}
        </Panel>
      ) : null}

      <Panel>
        <PanelHeading eyebrow="Evidence" title="What this analysis was built from" />
        <EvidencePanel evidence={result.evidence} dimensions={result.dimensions_analysed} />
      </Panel>
    </div>
  );
}
