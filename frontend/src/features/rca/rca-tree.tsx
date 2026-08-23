import { FileSearch } from "lucide-react";

import { STOP_REASON_LABELS } from "@/features/rca/evidence/decision-trace";
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
 *
 * Still entirely static. The stop reasons and drill-down captions added here are
 * text, and the links to evidence are plain in-page anchors, so none of it turns
 * the tree into an interactive widget.
 */

/**
 * A node, optionally carrying the evidence graph's extras.
 *
 * The persisted investigation's tree has `node_key` and `evidence_ids`; the
 * stateless RCA result does not. Optional rather than two components: everything
 * else about rendering a node is identical.
 */
type TreeNodeData = Omit<Driver, "children"> & {
  node_key?: string;
  evidence_ids?: string[];
  children: TreeNodeData[];
};

/**
 * How a node was broken down, or why it was not.
 *
 * `max_depth_reached` is deliberately excluded: every leaf at the depth limit
 * carries it, so per-node it is pure noise. It is stated once in the footnote
 * instead.
 */
function NodeNote({ node }: { node: TreeNodeData }) {
  if (node.children.length > 0) {
    if (!node.child_dimension) return null;
    return (
      <p className="rca-node-note muted">
        Broken down by <strong>{node.child_dimension}</strong>
        {node.child_split_type === "pure"
          ? " — the whole segment shares one value"
          : node.child_explanatory_power !== null
            ? ` — explains ${formatPercent(
                node.child_explanatory_power * 100,
                0,
              )} of how differently its parts moved`
            : ""}
      </p>
    );
  }

  if (!node.stop_reason || node.stop_reason === "max_depth_reached") return null;
  return (
    <p className="rca-node-stop">
      Not broken down further —{" "}
      {STOP_REASON_LABELS[node.stop_reason] ?? node.stop_reason.replace(/_/g, " ")}
    </p>
  );
}

function TreeNode({ node, scale }: { node: TreeNodeData; scale: number }) {
  const change = node.absolute_change ?? 0;
  const evidenceCount = node.evidence_ids?.length ?? 0;

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
          {/* Inside the label column on purpose, so the row's grid and the
              mobile reflow are untouched. A plain fragment link: no JavaScript,
              keyboard-reachable, and it survives printing. */}
          {evidenceCount > 0 && node.evidence_ids ? (
            <a
              className="rca-node-evidence"
              href={`#evidence-${node.evidence_ids[0]}`}
              aria-label={`Evidence for ${node.dimension} ${segmentLabel(node)}`}
            >
              <FileSearch size={13} aria-hidden="true" /> {evidenceCount}
            </a>
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

      <NodeNote node={node} />

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

/** Whether any branch ended because it hit the depth limit. */
function hitDepthLimit(node: TreeNodeData): boolean {
  if (node.children.length === 0) return node.stop_reason === "max_depth_reached";
  return node.children.some(hitDepthLimit);
}

export function RcaTree({ root, kpiName }: { root: TreeNodeData; kpiName: string }) {
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
        {hitDepthLimit(root)
          ? " Branches with no note below them stopped at the drill-down depth limit."
          : ""}
      </p>
    </div>
  );
}
