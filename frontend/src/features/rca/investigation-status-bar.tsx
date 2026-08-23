import { Pill } from "@/components/ui";
import { formatDate, formatDuration } from "@/lib/format";
import type { InvestigationStatus } from "@/types/investigation";

/**
 * Visible proof that this page is a snapshot rather than a fresh run.
 *
 * The id and the run time are the whole point: without them a reader cannot tell
 * whether the numbers were computed now or last week, which is exactly the
 * ambiguity persisting investigations exists to remove.
 */

const STATUS_LABELS: Record<InvestigationStatus, string> = {
  planned: "Planned",
  running: "Running",
  completed: "Completed",
  partial: "Partial",
  failed: "Failed",
};

const STATUS_TONE: Record<
  InvestigationStatus,
  "neutral" | "info" | "success" | "warning" | "danger"
> = {
  planned: "neutral",
  running: "info",
  completed: "success",
  // Not danger: partial means the decomposition succeeded and something optional
  // did not, which is a caveat rather than a failure.
  partial: "warning",
  failed: "danger",
};

export function InvestigationStatusBar({
  id,
  status,
  createdAt,
  executionTimeMs,
}: {
  id: string;
  status: InvestigationStatus;
  createdAt: string;
  executionTimeMs: number;
}) {
  return (
    <div className="toolbar">
      <Pill tone={STATUS_TONE[status]}>{STATUS_LABELS[status]}</Pill>
      <span className="muted">
        Run {formatDate(createdAt)} · {formatDuration(executionTimeMs)} ·{" "}
        <span className="mono">{id}</span>
      </span>
    </div>
  );
}
