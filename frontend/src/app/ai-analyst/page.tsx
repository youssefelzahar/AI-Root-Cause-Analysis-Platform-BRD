import { Database, Sparkles } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";
import { Alert, EmptyState, Panel } from "@/components/ui";
import { getAiHealth } from "@/lib/api/ai";
import { listDatasets } from "@/lib/api/datasets";
import { formatDate, formatNumber } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function AiAnalystPage() {
  // Only Analysis Ready datasets can be asked about: every answer is grounded in
  // an investigation, and an investigation needs a KPI definition.
  const [page, health] = await Promise.all([
    listDatasets({ status: "analysis_ready", limit: 50 }),
    getAiHealth(),
  ]);

  return (
    <div>
      <PageHeader
        eyebrow="AI analyst"
        title="Ask"
        description="Ask a question in plain English. The deterministic engines do the analysis; the model reads your question and explains what they found."
      />

      {/* Checked before anyone types, because a question is expensive to lose. The
          analysis still runs without the model - the answer arrives as a written
          summary of the evidence instead of generated prose. */}
      {!health.ok ? (
        <div style={{ marginBottom: 16 }}>
          <Alert tone="warning" title="The language model is not available">
            {health.message} Questions will still be answered from the evidence, but the
            wording will be assembled rather than written.
          </Alert>
        </div>
      ) : null}

      <Panel>
        {page.items.length === 0 ? (
          <EmptyState
            title="No dataset is ready to ask about"
            description="A dataset becomes Analysis Ready once you configure a KPI for it. The analyst needs that definition to know which measure a question is about."
            action={
              <Link className="btn" href="/datasets">
                <Database size={16} aria-hidden="true" /> Go to datasets
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
                      <Link href={`/ai-analyst/${dataset.id}`}>{dataset.name}</Link>
                    </td>
                    <td className="numeric">{formatNumber(dataset.row_count)}</td>
                    <td className="numeric">{formatNumber(dataset.column_count)}</td>
                    <td className="muted">{formatDate(dataset.created_at)}</td>
                    <td>
                      <Link className="btn btn-sm" href={`/ai-analyst/${dataset.id}`}>
                        <Sparkles size={15} aria-hidden="true" /> Ask a question
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
