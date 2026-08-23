import { Pill } from "@/components/ui";
import { formatPercent, formatStat } from "@/lib/format";
import type { EvidenceRecord } from "@/types/investigation";

/**
 * Every structured claim, with what backs it.
 *
 * Each record is an anchor target: the hierarchy links here by evidence id, so a
 * reader can go from a node in the tree to the claim behind it without the tree
 * becoming an interactive widget. Landing is signalled by CSS `:target` alone.
 */

const TYPE_LABELS: Record<string, string> = {
  kpi_change: "KPI change",
  dimension_change: "Segment movement",
  contribution: "Contribution",
  drill_down: "Drill-down",
  anomaly: "Anomaly",
  trend: "Trend",
  comparison: "Comparison",
  validation: "Validation",
  new_segment: "New segment",
  gone_segment: "Gone segment",
  offsetting_factor: "Offsetting factor",
  execution: "Execution",
  coverage: "Coverage",
  reconciliation: "Reconciliation",
};

const CONFIDENCE_TONE: Record<string, "success" | "warning" | "neutral"> = {
  high: "success",
  medium: "warning",
  low: "neutral",
};

export function EvidenceRecords({ records }: { records: EvidenceRecord[] }) {
  if (records.length === 0) return null;

  return (
    <div className="evidence-block">
      <p className="evidence-block-title">
        Evidence records — {records.length} structured claims
      </p>

      <div className="evidence-records">
        {records.map((record) => (
          <div key={record.id} id={`evidence-${record.id}`} className="evidence-record">
            <p className="evidence-record-claim">{record.claim}</p>
            <p className="evidence-record-meta">
              <span className="mono">
                {TYPE_LABELS[record.evidence_type] ?? record.evidence_type}
              </span>
              {record.contribution_percentage !== null
                ? ` · contributed ${formatPercent(record.contribution_percentage, 0)}`
                : ""}
              {record.absolute_change !== null
                ? ` · change ${formatStat(record.absolute_change)}`
                : ""}
              {` · ${record.analysis_tool}`}
              {/* A record with no statement is a derived one, and says so rather
                  than carrying a plausible-looking query. */}
              {record.query_sequence !== null
                ? ` · from query #${record.query_sequence}`
                : " · derived from other evidence"}
            </p>
            {record.confidence ? (
              <Pill tone={CONFIDENCE_TONE[record.confidence] ?? "neutral"}>
                {record.confidence} confidence
              </Pill>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}
