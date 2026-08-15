import Link from "next/link";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { Alert, EmptyState, Panel } from "@/components/ui";
import { ProfileTabs } from "@/features/datasets/profile/profile-tabs";
import { getDataset, getProfile, getValidation } from "@/lib/api/datasets";
import { ApiError } from "@/lib/api/errors";
import { formatDate, formatDuration } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function DataProfilePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ tab?: string }>;
}) {
  const { id } = await params;
  const { tab } = await searchParams;

  let dataset;
  try {
    dataset = await getDataset(id);
  } catch (error) {
    if (error instanceof ApiError && error.isNotFound) notFound();
    throw error;
  }

  // One fetch serves all four tabs; the tabs are client state so switching
  // between them costs no round trip.
  const [envelope, validation] = await Promise.all([getProfile(id), getValidation(id)]);

  return (
    <div>
      <PageHeader
        eyebrow="Data profile"
        title={dataset.name}
        description={
          envelope.profile
            ? `Generated ${formatDate(envelope.profile.generated_at)} in ${formatDuration(envelope.profile.duration_ms)}`
            : undefined
        }
        backHref={`/datasets/${id}`}
        backLabel={dataset.name}
        actions={
          <Link className="btn" href={`/datasets/${id}/kpi`}>
            KPI setup
          </Link>
        }
      />

      {envelope.state === "ready" && envelope.profile ? (
        <ProfileTabs
          profile={envelope.profile}
          columns={envelope.columns}
          validation={validation}
          initialTab={tab ?? null}
        />
      ) : envelope.state === "failed" ? (
        <Alert tone="danger" title="Profiling failed">
          {envelope.message ?? "The profile could not be generated."}
        </Alert>
      ) : (
        <Panel>
          <EmptyState
            title={envelope.state === "running" ? "Profiling in progress" : "Profile not generated yet"}
            description={
              envelope.state === "running"
                ? "This page will show the full profile once processing finishes. Refresh in a moment."
                : "Upload or regenerate the dataset to produce a profile."
            }
            action={
              <Link className="btn btn-secondary" href={`/datasets/${id}`}>
                Back to dataset
              </Link>
            }
          />
        </Panel>
      )}
    </div>
  );
}
