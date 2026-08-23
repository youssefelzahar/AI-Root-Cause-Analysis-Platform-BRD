import { Table2 } from "lucide-react";
import Link from "next/link";

import { DefinitionList } from "@/components/ui";
import type { InvestigationSource } from "@/types/investigation";

/**
 * What this analysis read.
 *
 * A nested disclosure rather than a dialog: this is six strings that already
 * arrived with the investigation, so a modal and a second request would be
 * over-engineering. The query trace earns its dialog because SQL is wide; this
 * does not.
 */
export function InvestigationSourcePanel({
  source,
  engineVersion,
  investigationId,
}: {
  source: InvestigationSource;
  engineVersion: string;
  investigationId: string;
}) {
  const items = [
    { label: "Dataset", value: source.dataset_name },
    { label: "Measure", value: <span className="mono">{source.measure_column}</span> },
    {
      label: "Time column",
      value: source.time_column ? <span className="mono">{source.time_column}</span> : "—",
    },
    { label: "Aggregation", value: source.aggregation },
    { label: "Compared against", value: source.comparison.replace(/_/g, " ") },
    {
      // The storage-key form, not a server path: a local temp path would differ
      // on every request and would never reproduce.
      label: "Relation read",
      value: <span className="mono">{source.source_relation}</span>,
    },
    { label: "Engine version", value: <span className="mono">{engineVersion}</span> },
    { label: "Investigation", value: <span className="mono">{investigationId}</span> },
  ];

  return (
    <details className="evidence-block">
      <summary className="evidence-block-title">View source</summary>
      <DefinitionList items={items} />
      <div className="form-actions">
        <Link
          className="btn btn-secondary btn-sm"
          href={`/datasets/${source.dataset_id}/profile`}
        >
          <Table2 size={15} /> View dataset profile
        </Link>
      </div>
    </details>
  );
}
