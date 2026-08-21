import { Activity, Database } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";
import { EmptyState, Panel } from "@/components/ui";
import { listDatasets } from "@/lib/api/datasets";
import { formatDate, formatNumber } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function AnomaliesPage() {
  // Only Analysis Ready datasets can be checked: the engine needs a KPI
  // definition to know which measure to build a history from.
  const page = await listDatasets({ status: "analysis_ready", limit: 50 });

  return (
    <div>
      <PageHeader
        eyebrow="Anomaly detection"
        title="Anomalies"
        description="Pick an Analysis Ready dataset to see whether its KPI is behaving unusually against its own history."
      />

      <Panel>
        {page.items.length === 0 ? (
          <EmptyState
            title="No dataset is ready to check"
            description="A dataset becomes Analysis Ready once you configure a KPI for it. Anomaly detection also needs that KPI to have a time column, since it compares a period against the ones before it."
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
                      <Link href={`/anomalies/${dataset.id}`}>{dataset.name}</Link>
                    </td>
                    <td className="numeric">{formatNumber(dataset.row_count)}</td>
                    <td className="numeric">{formatNumber(dataset.column_count)}</td>
                    <td className="muted">{formatDate(dataset.created_at)}</td>
                    <td>
                      <Link className="btn btn-sm" href={`/anomalies/${dataset.id}`}>
                        <Activity size={15} /> Check for anomalies
                      </Link>
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
