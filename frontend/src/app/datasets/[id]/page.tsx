import { BarChart3, Table2 } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import {
  Alert,
  DefinitionList,
  Panel,
  PanelHeading,
  StatTile,
  StatusPill,
  ValidationPill,
} from "@/components/ui";
import { DatasetStatusWatcher } from "@/features/datasets/dataset-status-watcher";
import { getDataset, getProfile, getValidation } from "@/lib/api/datasets";
import { ApiError } from "@/lib/api/errors";
import { formatBytes, formatDate, formatNumber, formatPercent } from "@/lib/format";
import { IN_PROGRESS_STATUSES } from "@/types/api";

export const dynamic = "force-dynamic";

export default async function DatasetDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let dataset;
  try {
    dataset = await getDataset(id);
  } catch (error) {
    // A deleted dataset should be a real 404, not a 500.
    if (error instanceof ApiError && error.isNotFound) notFound();
    throw error;
  }

  const [profileEnvelope, validation] = await Promise.all([getProfile(id), getValidation(id)]);
  const profile = profileEnvelope.profile;
  const stillWorking = IN_PROGRESS_STATUSES.includes(dataset.status);

  return (
    <div>
      <PageHeader
        eyebrow={dataset.source_type === "sqlserver" ? "SQL Server query" : "Uploaded file"}
        title={dataset.name}
        description={dataset.original_filename ?? undefined}
        backHref="/datasets"
        backLabel="Datasets"
        actions={
          <>
            <Link className="btn btn-secondary" href={`/datasets/${id}/profile`}>
              <Table2 size={16} /> View profile
            </Link>
            <Link className="btn" href={`/datasets/${id}/kpi`}>
              <BarChart3 size={16} /> KPI setup
            </Link>
          </>
        }
      />

      {stillWorking ? <DatasetStatusWatcher datasetId={id} /> : null}

      {dataset.status === "profiling_failed" || dataset.status === "upload_failed" ? (
        <Alert tone="danger" title="Processing failed">
          {dataset.error_message ?? "This dataset could not be processed."}
        </Alert>
      ) : null}

      {validation?.state === "blocked" ? (
        <Alert tone="danger" title="This dataset cannot be used for analysis">
          {validation.issues.find((issue) => issue.severity === "error")?.message ??
            "Review the Quality tab for details."}
        </Alert>
      ) : null}

      {dataset.analysis_ready ? (
        <Alert tone="success" title="Analysis Ready">
          A KPI definition is configured. This dataset can be handed to the RCA engine.
        </Alert>
      ) : null}

      <section className="stats-grid">
        <StatTile label="Size" value={formatBytes(dataset.size_bytes)} />
        <StatTile label="Rows" value={formatNumber(dataset.row_count)} />
        <StatTile label="Columns" value={formatNumber(dataset.column_count)} />
        <StatTile
          label="Missing cells"
          value={profile ? formatPercent(profile.missing_cell_pct) : "—"}
        />
      </section>

      <div className="content-grid">
        <Panel>
          <PanelHeading eyebrow="Metadata" title="Dataset details" />
          <DefinitionList
            items={[
              { label: "Status", value: <StatusPill status={dataset.status} /> },
              { label: "Quality", value: <ValidationPill state={dataset.quality_state} /> },
              { label: "Format", value: dataset.file_format },
              { label: "Source", value: dataset.source_type },
              { label: "Original filename", value: dataset.original_filename ?? "—" },
              { label: "Schema version", value: dataset.schema_version },
              {
                label: "Checksum (SHA-256)",
                value: (
                  <span className="mono">
                    {dataset.checksum_sha256 ? `${dataset.checksum_sha256.slice(0, 16)}…` : "—"}
                  </span>
                ),
              },
              { label: "Created", value: formatDate(dataset.created_at) },
              { label: "Updated", value: formatDate(dataset.updated_at) },
              {
                label: "Duplicate rows",
                value: profile
                  ? profile.duplicate_check_skipped
                    ? "Not checked (too many columns)"
                    : formatNumber(profile.duplicate_row_count)
                  : "—",
              },
            ]}
          />

          {dataset.source_query ? (
            <div style={{ marginTop: 20 }}>
              <p className="eyebrow">Source query</p>
              <pre className="mono" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
                {dataset.source_query}
              </pre>
            </div>
          ) : null}
        </Panel>

        <Panel>
          <PanelHeading eyebrow="Pipeline" title="Next steps" />
          <ol className="actions-list">
            <li>
              Review the <Link href={`/datasets/${id}/profile`}>data profile</Link> to see what the
              dataset contains.
            </li>
            <li>
              Check the{" "}
              <Link href={`/datasets/${id}/profile?tab=quality`}>quality report</Link> for
              conversion issues.
            </li>
            <li>
              Configure a <Link href={`/datasets/${id}/kpi`}>KPI</Link> to make the dataset
              Analysis Ready.
            </li>
          </ol>
        </Panel>
      </div>
    </div>
  );
}
