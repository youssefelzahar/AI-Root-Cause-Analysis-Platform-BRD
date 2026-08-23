import { Check, ChevronRight, Minus, X } from "lucide-react";
import type { ReactNode } from "react";

import { formatDuration, formatNumber, formatPercent } from "@/lib/format";
import type { EvidenceRecord, Investigation } from "@/types/investigation";
import type { Evidence as RcaEvidence } from "@/types/rca";

import { AuditTrail } from "./audit-trail";
import { DecisionTrace } from "./decision-trace";
import { DimensionExplainability } from "./dimension-explainability";
import { EvidenceQualityChecks, EvidenceQualityPill } from "./evidence-quality";
import { EvidenceRecords } from "./evidence-records";
import { ExecutionEvidence } from "./execution-evidence";
import { InvestigationSourcePanel } from "./investigation-source";
import { QueryTrace } from "./query-trace";

/**
 * Evidence & Validation: why you should trust the analysis above.
 *
 * A native `<details>`, not a client disclosure. The investigation page has no
 * client state at all, and the house boundary is "server page plus one small
 * island for the one thing that needs it" - a collapse is not that thing. The
 * element also brings keyboard operation, correct AT semantics, find-in-page
 * expansion and fragment-navigation expansion for free, all of which matter more
 * on an audit surface than anywhere else on the page.
 *
 * Open by default: the specification asks for collapsible, not collapsed, and
 * the evidence records are anchor targets for the hierarchy's links.
 *
 * The disclosure IS the "show details" control. Rendering a separate button
 * beside it would give two controls for one piece of state, and a button inside
 * `<summary>` would be nested interactive content whose clicks also toggle the
 * disclosure - so the actions live in the body.
 */

const RECONCILIATION_ICON: Record<string, ReactNode> = {
  passed: <Check size={13} aria-hidden="true" />,
  failed: <X size={13} aria-hidden="true" />,
  not_applicable: <Minus size={13} aria-hidden="true" />,
};

function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
}) {
  return (
    <span className="evidence-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {hint ? <small>{hint}</small> : null}
    </span>
  );
}

export function EvidenceSection({
  investigation,
  records,
}: {
  investigation: Investigation;
  records: EvidenceRecord[];
}) {
  const { execution, reconciliation, evidence_quality, decisions, source } = investigation;
  const rowsCompared =
    execution.rows_in_previous_period + execution.rows_in_current_period;

  // The RCA payload still carries the unparsed-row counts, which have no home on
  // the persisted execution metrics.
  const rcaEvidence = investigation.result?.evidence as RcaEvidence | undefined;

  return (
    <details className="evidence-section" open>
      <summary className="evidence-summary">
        <ChevronRight className="evidence-chevron" size={16} aria-hidden="true" />
        <span className="eyebrow">Evidence &amp; validation</span>
        <EvidenceQualityPill verdict={evidence_quality.verdict} />

        <Metric label="Rows scanned" value={formatNumber(execution.rows_scanned)} />
        <Metric
          label="Rows compared"
          value={formatNumber(rowsCompared)}
          hint={`${formatNumber(execution.rows_in_previous_period)} + ${formatNumber(
            execution.rows_in_current_period,
          )}`}
        />
        <Metric
          label="Queries executed"
          value={formatNumber(execution.queries_executed)}
          hint={formatDuration(execution.execution_time_ms)}
        />
        <Metric
          label="Reconciliation"
          value={
            <>
              {reconciliation.status
                ? RECONCILIATION_ICON[reconciliation.status] ?? null
                : null}{" "}
              {/* The tick comes only from the backend's verdict. Deriving it
                  here from contribution_sum would duplicate a tolerance the
                  backend owns, and the two would eventually disagree. */}
              {reconciliation.contribution_sum === null
                ? "—"
                : formatPercent(reconciliation.contribution_sum * 100, 1)}
            </>
          }
        />
      </summary>

      <div className="evidence-body">
        <EvidenceQualityChecks quality={evidence_quality} />

        <ExecutionEvidence
          execution={execution}
          reconciliation={reconciliation}
          unparsedTimeRows={rcaEvidence?.unparsed_time_rows}
          unparsedMeasureRows={rcaEvidence?.unparsed_measure_rows}
        />

        <DimensionExplainability
          dimensions={investigation.result?.dimensions_analysed ?? []}
        />

        <DecisionTrace decisions={decisions} />

        <EvidenceRecords records={records} />

        <div className="evidence-actions">
          <QueryTrace
            investigationId={investigation.id}
            queryCount={investigation.query_count}
          />
        </div>

        <InvestigationSourcePanel
          source={source}
          engineVersion={investigation.engine_version}
          investigationId={investigation.id}
        />

        <AuditTrail
          investigationId={investigation.id}
          eventCount={investigation.audit_event_count}
        />
      </div>
    </details>
  );
}
