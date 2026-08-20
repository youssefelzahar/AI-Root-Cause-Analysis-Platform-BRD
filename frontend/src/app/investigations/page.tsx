import { Database, LineChart } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";
import { EmptyState, Panel } from "@/components/ui";
import { DeleteInvestigationButton } from "@/features/rca/delete-investigation-button";
import { listDatasets } from "@/lib/api/datasets";
import { formatDate, formatNumber } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function InvestigationsPage() {
  // Only Analysis Ready datasets can be investigated: the engine needs a KPI
  // definition to know what to explain.
  const page = await listDatasets({ status: "analysis_ready", limit: 50 });

  return (
    <div>
      <PageHeader
        eyebrow="Root cause analysis"
        title="Investigations"
        description="Pick an Analysis Ready dataset to find out which segments drove its KPI change."
      />

      <Panel>
        {page.items.length === 0 ? (
          <EmptyState
            title="No dataset is ready to investigate"
            description="A dataset becomes Analysis Ready once you configure a KPI for it - the engine needs to know which measure, time column and dimensions to analyse."
            action={
              <Link className="btn" href="/datasets">
                <Database size={16} /> Go to datasets
              </Link>
            }
          />
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Dataset</th>
                  <th className="numeric">Rows</th>
                  <th className="numeric">Columns</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {page.items.map((dataset) => (
                  <tr key={dataset.id}>
                    <td>
                      <Link href={`/investigations/${dataset.id}`}>{dataset.name}</Link>
                    </td>
                    <td className="numeric">{formatNumber(dataset.row_count)}</td>
                    <td className="numeric">{formatNumber(dataset.column_count)}</td>
                    <td className="muted">{formatDate(dataset.created_at)}</td>
                    <td>
                      <div className="row-actions">
                        <Link className="btn btn-sm" href={`/investigations/${dataset.id}`}>
                          <LineChart size={15} /> Run analysis
                        </Link>
                        <DeleteInvestigationButton datasetId={dataset.id} name={dataset.name} />
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
