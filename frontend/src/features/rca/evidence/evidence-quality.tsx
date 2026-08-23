import { AlertTriangle, Check, Minus, X } from "lucide-react";
import type { ReactNode } from "react";

import { Pill } from "@/components/ui";
import type {
  EvidenceQuality,
  EvidenceQualityVerdict,
  QualityCheckStatus,
} from "@/types/investigation";

/**
 * Whether this analysis is well-formed and traceable.
 *
 * Kept visually quiet on purpose. Severity - how big the KPI move was - owns the
 * loud full-width alert band at the top of the page. Evidence quality is a small
 * pill and a checklist, because a failed check must not compete with a CRITICAL
 * KPI for a reader's attention. Never style this with `alert-danger`.
 */

/**
 * Not `ValidationPill`: that one is typed on the dataset schema verdict
 * (pass / warning / blocked) and is used on the profile page. Widening it would
 * couple two unrelated vocabularies and invite "the file is valid" to be read as
 * "the analysis is trustworthy" - the exact conflation this section exists to
 * prevent.
 */
const QUALITY: Record<
  EvidenceQualityVerdict,
  { tone: "success" | "warning" | "danger" | "neutral"; label: string }
> = {
  validated: { tone: "success", label: "Validated" },
  warning: { tone: "warning", label: "Warning" },
  failed: { tone: "danger", label: "Failed" },
  not_applicable: { tone: "neutral", label: "Not checked" },
};

export function EvidenceQualityPill({ verdict }: { verdict: EvidenceQualityVerdict | null }) {
  const entry = verdict ? QUALITY[verdict] : null;
  if (!entry) return <Pill tone="neutral">Not checked</Pill>;
  return <Pill tone={entry.tone}>{entry.label}</Pill>;
}

/** The six checks of the specification, in the order it lists them. */
const CHECK_LABELS: Record<string, string> = {
  data_period_coverage: "Data and period coverage",
  numerical_consistency: "Numerical consistency",
  contribution_reconciliation: "Contribution reconciliation",
  query_provenance: "Query provenance",
  source_traceability: "Source traceability",
  required_metadata: "Required metadata",
};

const CHECK_ORDER = Object.keys(CHECK_LABELS);

const CHECK_ICON: Record<QualityCheckStatus, ReactNode> = {
  passed: <Check size={14} />,
  warning: <AlertTriangle size={14} />,
  failed: <X size={14} />,
  not_applicable: <Minus size={14} />,
};

const CHECK_STATUS_LABELS: Record<QualityCheckStatus, string> = {
  passed: "Passed",
  warning: "Warning",
  failed: "Failed",
  not_applicable: "Not applicable",
};

export function EvidenceQualityChecks({ quality }: { quality: EvidenceQuality }) {
  if (quality.checks.length === 0) return null;

  // Fixed order regardless of what arrives, so the list reads the same way on
  // every investigation and two runs are comparable line by line.
  const ordered = [...quality.checks].sort(
    (a, b) => CHECK_ORDER.indexOf(a.check) - CHECK_ORDER.indexOf(b.check),
  );

  return (
    <div className="evidence-block">
      <p className="evidence-block-title">Evidence quality — six checks</p>

      <ul className="evidence-checks">
        {ordered.map((check) => (
          <li key={check.check} className={`evidence-check ${check.status}`}>
            <span className="evidence-check-icon" aria-hidden="true">
              {CHECK_ICON[check.status]}
            </span>
            <span className="evidence-check-name">
              {CHECK_LABELS[check.check] ?? check.check}
            </span>
            <span className="evidence-check-detail">
              {/* The icon is aria-hidden and colour alone is not a name. */}
              <span className="visually-hidden">
                {CHECK_STATUS_LABELS[check.status]}.{" "}
              </span>
              {check.detail}
            </span>
          </li>
        ))}
      </ul>

      {quality.caveats.length > 0 ? (
        <ul className="issue-list">
          {quality.caveats.map((caveat) => (
            <li key={caveat} className="issue warning">
              {caveat}
            </li>
          ))}
        </ul>
      ) : null}

      <p className="muted evidence-note">
        Evidence quality says whether this analysis is well-formed and traceable. It does not say
        whether the finding is important — that is the severity at the top of the page.
      </p>
    </div>
  );
}
