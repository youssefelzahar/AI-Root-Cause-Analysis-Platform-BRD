import { formatPercent, formatStat } from "@/lib/format";
import type { Decision } from "@/types/investigation";

/**
 * Why the system chose what it chose.
 *
 * Lives in Evidence rather than in the driver tables: all four facts behind a
 * selection - contribution, rank, absolute change, segment status - are already
 * in the table row, and restating them as prose there would double its width.
 * Explaining the choice is Evidence's job; showing the finding is the table's.
 */

/**
 * Why a branch of the drill-down ends where it does.
 *
 * Every value the engine emits, phrased to complete "Not broken down further -
 * ...". Exported because `rca-tree.tsx` renders the same labels next to the
 * branch that ended: one definition, two renderers.
 *
 * A `Record<string, string>` rather than a mapped type over a union: the engine
 * can add a reason before this file learns about it, and a raw
 * `contribution_immaterial` on screen beats a crash.
 */
export const STOP_REASON_LABELS: Record<string, string> = {
  max_depth_reached: "the drill-down depth limit was reached",
  no_dimensions_left: "every available dimension had already been used on this branch",
  residual_bucket:
    "this row is the grouped remainder of the smaller segments, so it has no single value to split by",
  contribution_immaterial: "its share of the change was too small to be worth splitting further",
  insufficient_rows:
    "there are too few rows behind it to say anything reliable about its parts",
  branching_limit: "other segments at this level explained more of the change",
  uniform_within_segment:
    "no remaining dimension divided it into segments that behaved differently",
};

/** What kind of choice the engine recorded. */
const DECISION_KIND_LABELS: Record<string, string> = {
  period_resolved: "Periods resolved",
  basis_selected: "Attribution basis",
  dimension_selected: "Dimension chosen",
  segment_selected: "Segment selected",
  drilldown_stopped: "Drill-down stopped",
  pattern_classified: "Change pattern",
  driver_suppressed: "Drivers suppressed",
};

/**
 * Numbers worth surfacing beside a decision, formatted the way the rest of the
 * page formats them. The engine sends structured inputs rather than a rendered
 * sentence precisely so this stays consistent.
 */
const SHARE_INPUTS = new Set(["contribution", "power", "top_contribution"]);

function factor(name: string, value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (SHARE_INPUTS.has(name) && typeof value === "number") {
    return `${label(name)} ${formatPercent(value * 100, 0)}`;
  }
  if (name === "rank" && typeof value === "number") return `rank #${value}`;
  if (typeof value === "number") return `${label(name)} ${formatStat(value)}`;
  if (typeof value === "boolean") return value ? label(name) : null;
  if (typeof value === "string") return `${label(name)} ${value}`;
  return null;
}

const FACTOR_LABELS: Record<string, string> = {
  contribution: "share of the change",
  rank: "rank",
  absolute_change: "absolute change",
  status: "status",
  power: "explains",
  top_contribution: "largest segment",
  rows: "rows",
  threshold_applied: "threshold",
  max_tree_depth: "depth limit",
  pareto_target: "concentration target",
  min_material_contribution: "materiality floor",
};

function label(name: string): string {
  return FACTOR_LABELS[name] ?? name.replace(/_/g, " ");
}

/** The inputs worth showing inline; the rest stay in the record. */
const SHOWN = ["threshold_applied", "rows", "depth", "max_tree_depth"];

export function DecisionTrace({ decisions }: { decisions: Decision[] }) {
  if (decisions.length === 0) return null;

  return (
    <div className="evidence-block">
      <p className="evidence-block-title">Decision trace — why these segments and dimensions</p>

      <ol className="decision-trace">
        {decisions.map((decision) => {
          const extras = SHOWN.map((key) => factor(key, decision.inputs?.[key])).filter(Boolean);
          return (
            <li key={decision.sequence} className="decision">
              <p className="decision-kind">
                {DECISION_KIND_LABELS[decision.kind] ?? decision.kind.replace(/_/g, " ")}
              </p>
              <p className="decision-subject">
                <strong>{decision.subject}</strong>
              </p>
              <p className="decision-why muted">
                Why: {decision.why}
                {extras.length > 0 ? ` (${extras.join(" · ")})` : ""}
              </p>
            </li>
          );
        })}
      </ol>

      <p className="muted evidence-note">
        Contribution measures how much a segment moved the total. It is not a claim that the
        segment caused the change.
      </p>
    </div>
  );
}
