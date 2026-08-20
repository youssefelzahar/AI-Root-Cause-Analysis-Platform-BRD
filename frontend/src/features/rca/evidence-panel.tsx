import { DefinitionList, Pill } from "@/components/ui";
import { formatDuration, formatNumber, formatPercent } from "@/lib/format";
import type { DimensionSummary, Evidence } from "@/types/rca";

/**
 * What the analysis was built from.
 *
 * `contribution_sum` should be exactly 100%: it is the invariant that every row
 * landed in exactly one segment, and showing it makes the guarantee checkable
 * rather than merely claimed.
 */
export function EvidencePanel({
  evidence,
  dimensions,
}: {
  evidence: Evidence;
  dimensions: DimensionSummary[];
}) {
  const items = [
    { label: "Rows scanned", value: formatNumber(evidence.total_rows) },
    {
      label: "Rows in the two periods",
      value: `${formatNumber(evidence.previous_rows)} + ${formatNumber(evidence.current_rows)}`,
    },
    { label: "Rows outside both periods", value: formatNumber(evidence.rows_outside_periods) },
    {
      label: "Contributions add up to",
      value:
        evidence.contribution_sum === null
          ? "—"
          : formatPercent(evidence.contribution_sum * 100, 1),
    },
    {
      label: "Queries run",
      value: `${formatNumber(evidence.statements_executed)} in ${formatDuration(evidence.duration_ms)}`,
    },
  ];

  if (evidence.unparsed_time_rows > 0) {
    items.push({ label: "Unreadable dates", value: formatNumber(evidence.unparsed_time_rows) });
  }
  if (evidence.unparsed_measure_rows > 0) {
    items.push({
      label: "Unreadable measure values",
      value: formatNumber(evidence.unparsed_measure_rows),
    });
  }

  return (
    <>
      <DefinitionList items={items} />

      {dimensions.length > 0 ? (
        <div className="table-wrap rca-dimension-table">
          <table className="data-table">
            <thead>
              <tr>
                <th>Dimension</th>
                <th className="numeric">Segments</th>
                <th className="numeric">Explains</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {dimensions.map((dimension) => (
                <tr key={dimension.dimension}>
                  <td>{dimension.dimension}</td>
                  <td className="numeric">{formatNumber(dimension.segment_count)}</td>
                  <td className="numeric">
                    {dimension.explanatory_power === null
                      ? "—"
                      : formatPercent(dimension.explanatory_power * 100, 0)}
                  </td>
                  <td>
                    {dimension.excluded_reason ? (
                      <Pill tone="neutral">{dimension.excluded_reason.replace(/_/g, " ")}</Pill>
                    ) : null}
                    {dimension.truncated ? <Pill tone="warning">Truncated</Pill> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted rca-tree-footnote">
            &ldquo;Explains&rdquo; is how far the segments deviate from all moving in proportion to
            their size. A dimension at 0% tells you nothing about where the change came from.
          </p>
        </div>
      ) : null}
    </>
  );
}
