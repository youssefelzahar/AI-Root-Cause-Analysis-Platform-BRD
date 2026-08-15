import Link from "next/link";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { Alert, EmptyState, Panel } from "@/components/ui";
import { KpiSetupForm } from "@/features/datasets/kpi/kpi-setup-form";
import {
  getActiveKpiDefinition,
  getDataset,
  getKpiCandidates,
  getProfile,
  getValidation,
} from "@/lib/api/datasets";
import { ApiError } from "@/lib/api/errors";

export const dynamic = "force-dynamic";

export default async function KpiSetupPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let dataset;
  try {
    dataset = await getDataset(id);
  } catch (error) {
    if (error instanceof ApiError && error.isNotFound) notFound();
    throw error;
  }

  const [candidates, validation, envelope, existing] = await Promise.all([
    getKpiCandidates(id),
    getValidation(id),
    getProfile(id),
    getActiveKpiDefinition(id),
  ]);

  const header = (
    <PageHeader
      eyebrow="KPI setup"
      title={dataset.name}
      description="Define exactly what the RCA engine should investigate."
      backHref={`/datasets/${id}`}
      backLabel={dataset.name}
    />
  );

  // A BLOCKED dataset gets an explanation and a route to the fix, not a form
  // that cannot succeed.
  if (validation?.state === "blocked") {
    const blockers = validation.issues.filter((issue) => issue.severity === "error").slice(0, 3);
    return (
      <div>
        {header}
        <Alert tone="danger" title="This dataset can't be analysed yet">
          Schema validation returned BLOCKED, so a KPI cannot be configured.
        </Alert>
        <Panel>
          <ul className="issue-list">
            {blockers.map((issue, index) => (
              <li className="issue error" key={`${issue.code}-${index}`}>
                <p className="issue-code">{issue.code}</p>
                <p>{issue.message}</p>
              </li>
            ))}
          </ul>
          <div className="form-actions" style={{ marginTop: 16 }}>
            <Link className="btn" href={`/datasets/${id}/profile?tab=quality`}>
              Review the quality report
            </Link>
          </div>
        </Panel>
      </div>
    );
  }

  if (!candidates || envelope.state !== "ready") {
    return (
      <div>
        {header}
        <Panel>
          <EmptyState
            title="Profile not ready"
            description="KPI candidates are derived from the data profile. Wait for profiling to finish, then come back."
            action={
              <Link className="btn btn-secondary" href={`/datasets/${id}`}>
                Back to dataset
              </Link>
            }
          />
        </Panel>
      </div>
    );
  }

  return (
    <div>
      {header}

      {validation?.state === "warning" ? (
        <Alert tone="warning" title="This dataset has quality issues">
          It can still be analysed. Review the{" "}
          <Link href={`/datasets/${id}/profile?tab=quality`}>quality report</Link> to see what was
          flagged.
        </Alert>
      ) : null}

      <KpiSetupForm
        datasetId={id}
        candidates={candidates}
        columns={envelope.columns}
        existing={existing}
      />
    </div>
  );
}
