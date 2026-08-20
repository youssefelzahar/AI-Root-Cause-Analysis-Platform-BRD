import { Database, Plus, Server } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";
import { EmptyState, Panel, StatusPill, ValidationPill } from "@/components/ui";
import { DeleteDatasetButton } from "@/features/datasets/delete-dataset-button";
import { listDatasets } from "@/lib/api/datasets";
import { formatBytes, formatDate, formatNumber } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function DatasetsPage() {
  // Errors propagate to error.tsx rather than being swallowed into a fallback.
  const page = await listDatasets({ limit: 50 });

  return (
    <div>
      <PageHeader
        eyebrow="Data foundation"
        title="Datasets"
        description="Upload a file or save a SQL Server query, then profile it and configure a KPI."
        actions={
          <>
            <Link className="btn btn-secondary" href="/sql">
              <Server size={16} /> Connect SQL Server
            </Link>
            <Link className="btn" href="/datasets/upload">
              <Plus size={16} /> Upload dataset
            </Link>
          </>
        }
      />

      <Panel>
        {page.items.length === 0 ? (
          <EmptyState
            title="No datasets yet"
            description="Upload a CSV or Excel file (up to 200 MB), or run a SQL Server query and save its output."
            action={
              <Link className="btn" href="/datasets/upload">
                <Database size={16} /> Upload your first dataset
              </Link>
            }
          />
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Source</th>
                  <th className="numeric">Size</th>
                  <th className="numeric">Rows</th>
                  <th className="numeric">Columns</th>
                  <th>Quality</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {page.items.map((dataset) => (
                  <tr key={dataset.id}>
                    <td>
                      <Link href={`/datasets/${dataset.id}`}>{dataset.name}</Link>
                    </td>
                    <td className="muted">{dataset.source_type}</td>
                    <td className="numeric">{formatBytes(dataset.size_bytes)}</td>
                    <td className="numeric">{formatNumber(dataset.row_count)}</td>
                    <td className="numeric">{formatNumber(dataset.column_count)}</td>
                    <td>
                      <ValidationPill state={dataset.quality_state} />
                    </td>
                    <td>
                      <StatusPill status={dataset.status} />
                    </td>
                    <td className="muted">{formatDate(dataset.created_at)}</td>
                    <td>
                      <div className="row-actions">
                        <Link className="btn btn-ghost btn-sm" href={`/datasets/${dataset.id}/profile`}>
                          View profile
                        </Link>
                        <DeleteDatasetButton
                          datasetId={dataset.id}
                          name={dataset.name}
                          status={dataset.status}
                        />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
