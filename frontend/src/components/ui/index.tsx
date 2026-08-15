import type { ReactNode } from "react";

import { cn } from "@/lib/format";
import type { DatasetStatus, ValidationState } from "@/types/api";

/* ------------------------------------------------------------------ card */
export function Panel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <section className={cn("panel", className)}>{children}</section>;
}

export function PanelHeading({
  eyebrow,
  title,
  actions,
}: {
  eyebrow?: string;
  title: string;
  actions?: ReactNode;
}) {
  return (
    <div className="panel-heading">
      <div>
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h2>{title}</h2>
      </div>
      {actions}
    </div>
  );
}

/* ------------------------------------------------------------- stat tile */
export function StatTile({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "neutral" | "positive" | "negative";
}) {
  return (
    <div className={cn("stat-tile", tone !== "neutral" && tone)}>
      <span>{label}</span>
      <strong>{value}</strong>
      {hint ? <small>{hint}</small> : null}
    </div>
  );
}

/* ------------------------------------------------------------------ pill */
type Tone = "neutral" | "success" | "warning" | "danger" | "info";

const VALIDATION_TONE: Record<ValidationState, Tone> = {
  pass: "success",
  warning: "warning",
  blocked: "danger",
};

const STATUS_TONE: Record<DatasetStatus, Tone> = {
  pending_upload: "neutral",
  uploaded: "neutral",
  validating: "info",
  profiling: "info",
  profiled: "success",
  analysis_ready: "success",
  upload_failed: "danger",
  profiling_failed: "danger",
  blocked: "danger",
};

export function Pill({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={cn("pill", `pill-${tone}`)}>{children}</span>;
}

/** PASS / WARNING / BLOCKED (PRD section 10). */
export function ValidationPill({ state }: { state: ValidationState | null | undefined }) {
  if (!state) return <Pill tone="neutral">Not checked</Pill>;
  return <Pill tone={VALIDATION_TONE[state]}>{state}</Pill>;
}

export function StatusPill({ status }: { status: DatasetStatus }) {
  const labels: Record<DatasetStatus, string> = {
    pending_upload: "Pending",
    uploaded: "Uploaded",
    validating: "Validating",
    profiling: "Profiling",
    profiled: "Profiled",
    analysis_ready: "Analysis Ready",
    upload_failed: "Upload failed",
    profiling_failed: "Profiling failed",
    blocked: "Blocked",
  };
  return <Pill tone={STATUS_TONE[status]}>{labels[status]}</Pill>;
}

export function Badge({
  children,
  recommended,
}: {
  children: ReactNode;
  recommended?: boolean;
}) {
  return <span className={cn("badge", recommended && "badge-recommended")}>{children}</span>;
}

/* ----------------------------------------------------------------- alert */
export function Alert({
  tone = "info",
  title,
  children,
}: {
  tone?: "info" | "warning" | "danger" | "success";
  title?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("alert", `alert-${tone}`)} role={tone === "danger" ? "alert" : undefined}>
      <div>
        {title ? <strong>{title}</strong> : null}
        <p>{children}</p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ empty/skel */
export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <h2>{title}</h2>
      {description ? <p>{description}</p> : null}
      {action}
    </div>
  );
}

export function TableSkeleton({ rows = 5, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div className="table-wrap" aria-busy="true" aria-label="Loading">
      <table className="data-table">
        <thead>
          <tr>
            {Array.from({ length: columns }).map((_, index) => (
              <th key={index}>
                <div className="skeleton" style={{ height: 10, width: "70%" }} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }).map((_, rowIndex) => (
            <tr key={rowIndex}>
              {Array.from({ length: columns }).map((_, cellIndex) => (
                <td key={cellIndex}>
                  <div className="skeleton" style={{ height: 12, width: "80%" }} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CardSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="stats-grid" aria-busy="true" aria-label="Loading">
      {Array.from({ length: count }).map((_, index) => (
        <div className="stat-tile" key={index}>
          <div className="skeleton" style={{ height: 10, width: "50%" }} />
          <div className="skeleton" style={{ height: 22, width: "70%" }} />
        </div>
      ))}
    </div>
  );
}

/* --------------------------------------------------------- progress bar */
export function ProgressBar({
  value,
  indeterminate,
  label,
}: {
  value?: number;
  indeterminate?: boolean;
  label?: string;
}) {
  return (
    <div
      className="progress"
      role="progressbar"
      aria-label={label}
      aria-valuenow={indeterminate ? undefined : Math.round(value ?? 0)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className={cn("progress-bar", indeterminate && "indeterminate")}
        style={indeterminate ? undefined : { width: `${Math.min(100, Math.max(0, value ?? 0))}%` }}
      />
    </div>
  );
}

/* --------------------------------------------------- definition list */
export function DefinitionList({ items }: { items: Array<{ label: string; value: ReactNode }> }) {
  return (
    <dl className="definition-list">
      {items.map((item) => (
        <div key={item.label}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
