import { AlertTriangle, BarChart3, RefreshCw, Table2 } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { Alert, Panel, PanelHeading } from "@/components/ui";
import { DeleteInvestigationButton } from "@/features/rca/delete-investigation-button";
import { DriverTable } from "@/features/rca/driver-table";
import { EvidenceSection } from "@/features/rca/evidence/evidence-section";
import { InvestigationNotices } from "@/features/rca/investigation-notices";
import { InvestigationStatusBar } from "@/features/rca/investigation-status-bar";
import { KpiSummary } from "@/features/rca/kpi-summary";
import { RcaTree } from "@/features/rca/rca-tree";
import { Waterfall } from "@/features/rca/waterfall";
import { ApiError } from "@/lib/api/errors";
import { getInvestigation, listInvestigationEvidence } from "@/lib/api/investigations";
import type { Investigation } from "@/types/investigation";
import type { Notice, Severity } from "@/types/rca";

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

/**
 * One persisted investigation.
 *
 * A snapshot: everything here is read from the stored row, so refreshing or
 * sharing the link shows the same numbers rather than re-running the analysis.
 * The findings come first and Evidence & Validation last, as a visually separate
 * band - the page answers "what did we find?" and the band answers "why should I
 * trust it?".
 */
export default async function InvestigationPage({
  params,
}: {
  params: Promise<{ datasetId: string; investigationId: string }>;
}) {
  const { datasetId, investigationId } = await params;

  let investigation: Investigation;
  let records;
  try {
    [investigation, records] = await Promise.all([
      getInvestigation(investigationId),
      listInvestigationEvidence(investigationId),
    ]);
  } catch (error) {
    // A dead permalink is a 404, not a workflow step.
    if (error instanceof ApiError && error.isNotFound) notFound();
    throw error;
  }

  const rerunHref = `/investigations/${datasetId}`;
  const header = (
    <PageHeader
      eyebrow="Root cause analysis"
      title={`${investigation.source.dataset_name} — ${investigation.result?.kpi.name ?? investigation.source.measure_column}`}
      description={investigation.result?.summary ?? investigation.question ?? undefined}
      backHref="/investigations"
      backLabel="Investigations"
      actions={
        <>
          {/* prefetch={false}: this link runs an analysis, and a viewport
              prefetch would create one without anybody clicking. */}
          <Link className="btn btn-secondary" href={rerunHref} prefetch={false}>
            <RefreshCw size={16} /> Re-run
          </Link>
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
            name={investigation.source.dataset_name}
            redirectTo="/investigations"
          />
        </>
      }
    />
  );

  const statusBar = (
    <InvestigationStatusBar
      id={investigation.id}
      status={investigation.status}
      createdAt={investigation.created_at}
      executionTimeMs={investigation.execution.execution_time_ms}
    />
  );

  // A failed run still shows its evidence: the audit trail and the quality
  // checks are the most useful thing about it, and discarding them would leave a
  // reader with nothing but an error string.
  if (investigation.status === "failed" || investigation.result === null) {
    return (
      <div>
        {header}
        {statusBar}
        <Panel>
          <PanelHeading eyebrow="Failed" title="This investigation did not finish" />
          <p>
            {investigation.error_message ??
              "The analysis stopped before it produced a result."}
          </p>
          {investigation.error_code ? (
            <p className="muted">
              Error code <span className="mono">{investigation.error_code}</span>
            </p>
          ) : null}
          <div className="form-actions">
            <Link className="btn" href={rerunHref} prefetch={false}>
              <RefreshCw size={16} /> Run it again
            </Link>
          </div>
        </Panel>
        <EvidenceSection investigation={investigation} records={records.items} />
      </div>
    );
  }

  const result = investigation.result;
  const { kpi, attribution, periods } = result;

  const attributable = attribution.basis !== "unattributable";
  const showEffects = attribution.basis === "mix_rate";
  const explanation = STATE_EXPLANATION[result.state];
  const stillRunning =
    investigation.status === "planned" || investigation.status === "running";

  return (
    <div>
      {header}
      {statusBar}

      {stillRunning ? (
        <Alert tone="info" title="This investigation is still running">
          Reload the page to check again.
        </Alert>
      ) : null}

      {investigation.status === "partial" && investigation.limitations.length > 0 ? (
        // A raw div rather than <Alert>: that wraps its children in a <p>, and
        // this needs a list.
        <div className="alert alert-warning">
          <div>
            <strong>This investigation is incomplete</strong>
            <ul className="issue-list">
              {investigation.limitations.map((limitation) => (
                <li key={limitation} className="issue warning">
                  {limitation}
                </li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}

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
          <InvestigationNotices notices={result.notices as Notice[]} />
        </Panel>
      ) : null}

      {attributable && result.primary_drivers.length > 0 ? (
        <Panel>
          <PanelHeading eyebrow="Contribution" title={`From ${kpi.name} previous to current`} />
          <Waterfall
            kpi={kpi}
            drivers={[...result.primary_drivers, ...result.secondary_drivers]}
          />
        </Panel>
      ) : null}

      {/* Primary, then secondary, then offsetting, then the hierarchy. Stacked
          rather than side by side: the three groups are read in that order
          because each is a weaker claim than the one above it, and a two-column
          split invites reading the second column first. Every group renders
          even when empty - "no segment pushed the other way" is a finding, and
          hiding the panel makes its absence look like a missing feature. */}
      <Panel>
        <PanelHeading
          eyebrow="Primary drivers"
          title="Segments accounting for most of the change"
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
        <PanelHeading eyebrow="Secondary drivers" title="Smaller contributors" />
        <DriverTable
          drivers={result.secondary_drivers}
          showEffects={showEffects}
          emptyMessage={
            attributable
              ? "Every material contributor is already listed as a primary driver."
              : "Contributions cannot be attributed for this aggregation."
          }
        />
      </Panel>

      <Panel>
        <PanelHeading eyebrow="Offsetting factors" title="Segments that pushed the other way" />
        <DriverTable
          drivers={result.offsetting_factors}
          showEffects={showEffects}
          emptyMessage="Every segment moved in the same direction as the KPI."
        />
      </Panel>

      {investigation.tree ?? result.rca_tree ? (
        <Panel>
          <PanelHeading eyebrow="Hierarchy" title="Where the change concentrates" />
          {/* The persisted tree, so every node can link to the evidence behind
              it. result.rca_tree is the same hierarchy without those links. */}
          <RcaTree root={investigation.tree ?? result.rca_tree!} kpiName={kpi.name} />
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

      <EvidenceSection investigation={investigation} records={records.items} />
    </div>
  );
}
