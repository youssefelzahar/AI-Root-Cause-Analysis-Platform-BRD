"use client";

import Link from "next/link";
import type { ReactNode } from "react";

/**
 * The body shared by both investigation error boundaries.
 *
 * Hoisted so the launcher and the permalink do not drift into two near-identical
 * copies with different copy - which is how the anomaly and investigation
 * blocked components ended up diverging.
 *
 * Genuine failures only. Expected outcomes - no KPI yet, only one period of
 * data, a column that has since been dropped - are handled inside the pages,
 * because Next sanitises server-component errors in production and the error
 * code would not survive this boundary.
 */
export function InvestigationError({
  title,
  message,
  reset,
  actions,
}: {
  title: string;
  message: string;
  reset: () => void;
  actions?: ReactNode;
}) {
  return (
    <div>
      <div className="alert alert-danger" role="alert">
        <div>
          <strong>{title}</strong>
          <p>{message}</p>
        </div>
      </div>
      <div className="form-actions">
        <button type="button" className="btn" onClick={reset}>
          Try again
        </button>
        {actions}
        <Link className="btn btn-ghost" href="/investigations">
          Back to investigations
        </Link>
      </div>
    </div>
  );
}
