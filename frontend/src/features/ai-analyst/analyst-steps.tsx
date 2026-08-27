import { CheckCircle2, CircleAlert, Loader2 } from "lucide-react";

import { cn, formatDuration } from "@/lib/format";
import type { AnalystStep } from "@/types/ai";

/**
 * What the analyst did, as a stage list.
 *
 * Reuses the upload pipeline's `.stage-list` shape rather than inventing a second
 * progress idiom: same classes, same icon-per-state rule, same
 * colour-plus-weight pairing so state is never carried by colour alone.
 *
 * The stages are **fixed and known before the request**, because the plan is a
 * fixed recipe rather than something the model writes. So this can show the whole
 * sequence greyed out while the request is in flight and light each one up as the
 * response reports it - which is only honest because the backend cannot decide to
 * do something else halfway through.
 */

/** Every tool, in the order the longest recipe runs them. */
const STAGES = [
  { key: "get_kpi_result", label: "Measure the KPI change" },
  { key: "detect_anomaly", label: "Check whether it is unusual" },
  { key: "dimension_analysis", label: "Break down every dimension" },
  { key: "contribution_analysis", label: "Rank the contributors" },
  { key: "drill_down", label: "Drill into the strongest branch" },
  { key: "get_investigation", label: "Read the investigation" },
  { key: "get_evidence", label: "Collect the evidence" },
] as const;

const LABELS: Record<string, string> = Object.fromEntries(
  STAGES.map((stage) => [stage.key, stage.label]),
);

/**
 * Derived from the response, never stored - the same rule
 * `upload-workspace.stageStateFor` follows.
 */
function stateFor(step: AnalystStep): "done" | "failed" {
  return step.ok ? "done" : "failed";
}

export function AnalystSteps({
  steps,
  running,
}: {
  steps: AnalystStep[];
  running: boolean;
}) {
  // While running, show the recipe's shape so the wait has a scale. The exact
  // sequence depends on the intent, which is not known until the model answers,
  // so this is the superset rather than a prediction.
  if (running) {
    return (
      <ol className="stage-list">
        <li className="stage active">
          <Loader2 size={16} aria-hidden="true" /> Read the question
        </li>
        {STAGES.slice(0, 5).map((stage) => (
          <li key={stage.key} className="stage">
            <span style={{ width: 16 }} />
            {stage.label}
          </li>
        ))}
      </ol>
    );
  }

  if (steps.length === 0) return null;

  return (
    <ol className="stage-list">
      {steps.map((step, index) => {
        const state = stateFor(step);
        return (
          <li key={`${step.tool}-${index}`} className={cn("stage", state)}>
            {state === "done" ? (
              <CheckCircle2 size={16} aria-hidden="true" />
            ) : (
              <CircleAlert size={16} aria-hidden="true" />
            )}
            {LABELS[step.tool] ?? step.tool.replace(/_/g, " ")}
            <span className="muted analyst-step-time">
              {formatDuration(step.duration_ms)}
            </span>
            {/* A step that ran but could not answer what was asked says so here -
                a segment missing from the hierarchy is a finding, not an error. */}
            {step.detail ? <span className="muted">— {step.detail}</span> : null}
          </li>
        );
      })}
    </ol>
  );
}
