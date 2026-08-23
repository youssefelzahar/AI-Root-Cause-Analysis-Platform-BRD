import { DefinitionList } from "@/components/ui";
import { formatDuration, formatNumber, formatPercent } from "@/lib/format";
import type { Execution, Reconciliation } from "@/types/investigation";

/**
 * What the analysis was built from, promoted from a UI stat into formal
 * evidence.
 *
 * Moved out of the old evidence-panel.tsx unchanged in substance: the same
 * numbers, now read from the persisted investigation rather than from a
 * recomputed result.
 */
export function ExecutionEvidence({
  execution,
  reconciliation,
  unparsedTimeRows,
  unparsedMeasureRows,
}: {
  execution: Execution;
  reconciliation: Reconciliation;
  unparsedTimeRows?: number;
  unparsedMeasureRows?: number;
}) {
  const items: Array<{ label: string; value: React.ReactNode }> = [
    { label: "Rows scanned", value: formatNumber(execution.rows_scanned) },
    {
      label: "Rows in the two periods",
      value: `${formatNumber(execution.rows_in_previous_period)} + ${formatNumber(
        execution.rows_in_current_period,
      )}`,
    },
    {
      label: "Rows outside both periods",
      value: formatNumber(execution.rows_outside_periods),
    },
    {
      label: "Contributions add up to",
      value:
        reconciliation.contribution_sum === null
          ? "—"
          : formatPercent(reconciliation.contribution_sum * 100, 1),
    },
    {
      label: "Queries run",
      value: `${formatNumber(execution.queries_executed)} in ${formatDuration(
        execution.execution_time_ms,
      )}`,
    },
  ];

  if (reconciliation.tolerance !== null) {
    items.push({
      label: "Reconciliation tolerance",
      // Shown because it is configurable: a raised tolerance has to be visible
      // in the record rather than only in the environment.
      value: <span className="mono">{reconciliation.tolerance}</span>,
    });
  }
  if (unparsedTimeRows) {
    items.push({ label: "Unreadable dates", value: formatNumber(unparsedTimeRows) });
  }
  if (unparsedMeasureRows) {
    items.push({
      label: "Unreadable measure values",
      value: formatNumber(unparsedMeasureRows),
    });
  }

  return (
    <div className="evidence-block">
      <p className="evidence-block-title">Execution</p>
      <DefinitionList items={items} />
    </div>
  );
}
