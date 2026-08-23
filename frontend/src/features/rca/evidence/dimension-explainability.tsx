import { Pill } from "@/components/ui";
import { formatNumber, formatPercent } from "@/lib/format";
import type { DimensionSummary } from "@/types/rca";

/**
 * How differently each dimension's segments behaved.
 *
 * This is NOT a contribution, and the two must never share a table. A
 * contribution is a share of the change and cannot exceed 100%; explainability
 * measures how far the segments deviated from all moving in proportion to their
 * size, so movements in opposite directions add to it without adding to the net
 * change. It routinely exceeds 100% and that is not an error.
 *
 * Three things enforce the distinction visually: its own labelled block, a
 * one-way bar rather than the diverging bar used for contributions, and an
 * explicit off-scale marker plus footnote when a value passes 100%.
 */

/** Why a dimension contributed no drivers, in the user's terms. */
const EXCLUDED_REASON_LABELS: Record<string, string> = {
  column_missing: "not in this file",
};

export function DimensionExplainability({ dimensions }: { dimensions: DimensionSummary[] }) {
  if (dimensions.length === 0) return null;

  // Strongest first, excluded last: insertion order can leave the informative
  // dimension third, which buries the finding.
  const ordered = [...dimensions].sort((a, b) => {
    if (a.excluded_reason && !b.excluded_reason) return 1;
    if (!a.excluded_reason && b.excluded_reason) return -1;
    return (b.explanatory_power ?? -1) - (a.explanatory_power ?? -1);
  });

  const anyOverHundred = ordered.some((d) => (d.explanatory_power ?? 0) > 1);

  return (
    <div className="evidence-block">
      <p className="evidence-block-title">
        Dimension explainability — how differently the segments behaved
      </p>

      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Dimension</th>
              <th className="numeric">Segments</th>
              <th className="numeric">Explains</th>
              <th />
              <th />
            </tr>
          </thead>
          <tbody>
            {ordered.map((dimension) => {
              const power = dimension.explanatory_power;
              const overHundred = (power ?? 0) > 1;
              return (
                <tr key={dimension.dimension}>
                  <td>{dimension.dimension}</td>
                  <td className="numeric">{formatNumber(dimension.segment_count)}</td>
                  <td className="numeric">
                    {/* Printed in full, never clamped: the number is the finding. */}
                    {power === null ? "—" : formatPercent(power * 100, 0)}
                  </td>
                  <td>
                    {power === null ? null : (
                      <div
                        className={
                          overHundred ? "freq-bar evidence-power-over" : "freq-bar"
                        }
                        aria-hidden="true"
                      >
                        {/* Only the width is clamped, so a value past 100% shows
                            as a full bar with the off-scale marker beside it. */}
                        <span style={{ width: `${Math.min(100, power * 100)}%` }} />
                      </div>
                    )}
                  </td>
                  <td>
                    {dimension.excluded_reason ? (
                      <Pill tone="neutral">
                        {EXCLUDED_REASON_LABELS[dimension.excluded_reason] ??
                          dimension.excluded_reason.replace(/_/g, " ")}
                      </Pill>
                    ) : null}
                    {dimension.truncated ? <Pill tone="warning">Truncated</Pill> : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="muted evidence-note">
        &ldquo;Explains&rdquo; is how far the segments deviate from all moving in proportion to
        their size. A dimension at 0% tells you nothing about where the change came from. It is
        not a contribution: those are in the driver tables above.
      </p>

      {anyOverHundred ? (
        <p className="muted evidence-note">
          Explains can exceed 100%. It measures deviation from proportional movement, and segments
          moving in opposite directions add to that deviation without adding to the net change. It
          is not a share of anything, so there is nothing for it to be capped at.
        </p>
      ) : null}
    </div>
  );
}
