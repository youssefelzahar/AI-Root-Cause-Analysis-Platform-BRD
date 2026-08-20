import { formatPercent, formatStat } from "@/lib/format";
import type { Driver } from "@/types/rca";

import { ContributionBar } from "./contribution-bar";
import { segmentLabel } from "./driver-table";

/**
 * Nested lists rather than SVG.
 *
 * Depth is capped at 3 and fanout at 3, so there is no layout problem SVG would
 * solve - while a real list gets nesting announced by screen readers, wraps long
 * user-supplied labels for free, and prints correctly.
 */
function TreeNode({ node, scale }: { node: Driver; scale: number }) {
  const change = node.absolute_change ?? 0;

  return (
    <li className="rca-node">
      <div className="rca-node-row">
        <span className="rca-node-label">
          <span className="muted">{node.dimension}</span> <strong>{segmentLabel(node)}</strong>
          {node.is_pure_split ? (
            <span className="muted rca-node-hint">
              {" "}
              — the whole segment shares this value
            </span>
          ) : null}
        </span>
        <span className={`rca-node-delta ${change < 0 ? "negative-text" : "positive-text"}`}>
          {formatStat(node.absolute_change)}
        </span>
        <span className="rca-node-share">
          {node.contribution === null ? "—" : formatPercent(node.contribution * 100, 0)}
        </span>
        <ContributionBar value={node.contribution} scale={scale} />
      </div>

      {node.children.length > 0 ? (
        <>
          {node.unexplained_share !== null && Math.abs(node.unexplained_share) > 0.01 ? (
            <p className="rca-node-note muted">
              {formatPercent(Math.abs(node.unexplained_share) * 100, 0)} of this segment&rsquo;s
              change is not accounted for below.
            </p>
          ) : null}
          <ul className="rca-tree">
            {node.children.map((child) => (
              <TreeNode key={child.node_id} node={child} scale={scale} />
            ))}
          </ul>
        </>
      ) : null}
    </li>
  );
}

export function RcaTree({ root, kpiName }: { root: Driver; kpiName: string }) {
  const scale = Math.max(
    ...root.children.map((child) => Math.abs(child.contribution ?? 0)),
    0.0001,
  );

  return (
    <div>
      <p className="rca-tree-root">
        <strong>{kpiName}</strong>{" "}
        <span className="muted">
          {formatStat(root.absolute_change)} — broken down by {root.child_dimension}
        </span>
      </p>
      <ul className="rca-tree">
        {root.children.map((child) => (
          <TreeNode key={child.node_id} node={child} scale={scale} />
        ))}
      </ul>
      <p className="muted rca-tree-footnote">
        Percentages are each segment&rsquo;s share of the total {kpiName} change, at every level —
        so they stay comparable across branches.
      </p>
    </div>
  );
}
