import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { Alert, Panel } from "@/components/ui";
import { AnalystBlocked } from "@/features/ai-analyst/analyst-blocked";
import { AnalystWorkspace } from "@/features/ai-analyst/analyst-workspace";
import { getAiHealth } from "@/lib/api/ai";
import { ApiError } from "@/lib/api/errors";
import { getActiveKpiDefinition, getDataset } from "@/lib/api/datasets";

export const dynamic = "force-dynamic";

/**
 * The asking surface for one dataset.
 *
 * A server component that fetches context and renders one client island - the
 * house boundary. Nothing is analysed during this render: unlike
 * `/investigations/[datasetId]`, which POSTs while rendering and redirects to a
 * permalink, a question has to be typed first. So this route is safe to prefetch
 * and cheap to land on.
 */
export default async function AiAnalystDatasetPage({
  params,
}: {
  params: Promise<{ datasetId: string }>;
}) {
  const { datasetId } = await params;

  let dataset;
  try {
    dataset = await getDataset(datasetId);
  } catch (error) {
    if (error instanceof ApiError && error.isNotFound) notFound();
    throw error;
  }

  // Both tolerate absence: the KPI helper returns null on 404, and health never
  // throws. Neither should stop the page rendering.
  const [kpi, health] = await Promise.all([
    getActiveKpiDefinition(datasetId),
    getAiHealth(),
  ]);

  const ready = dataset.status === "analysis_ready";

  return (
    <div>
      <PageHeader
        eyebrow="AI analyst"
        title={dataset.name}
        description={
          kpi
            ? `Questions are answered about ${kpi.name} (${kpi.aggregation} of ${kpi.column_name}), compared ${kpi.comparison.replace(/_/g, " ")}.`
            : "Configure a KPI to make this dataset answerable."
        }
        backHref="/ai-analyst"
        backLabel="All datasets"
      />

      {/* Rendered inline rather than thrown, because Next sanitises
          server-component errors in production and the code would not survive. */}
      {!ready ? (
        <Panel>
          <AnalystBlocked code="DATASET_NOT_ANALYSIS_READY" />
        </Panel>
      ) : (
        <>
          {!health.ok ? (
            <div style={{ marginBottom: 16 }}>
              <Alert tone="warning" title="The language model is not available">
                {health.message} The analysis still runs and every number is still
                measured - the wording will be assembled from the evidence rather than
                written.
              </Alert>
            </div>
          ) : null}

          <AnalystWorkspace datasetId={datasetId} kpiName={kpi?.name ?? null} />
        </>
      )}
    </div>
  );
}
