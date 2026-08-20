"use client";

import Link from "next/link";

/**
 * Genuine failures only.
 *
 * Expected outcomes - no KPI yet, only one period of data, a column that has
 * since been dropped - are handled inside the page, because Next.js sanitises
 * server-component errors in production and the error code would not survive
 * this boundary.
 */
export default function InvestigationError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div>
      <div className="alert alert-danger" role="alert">
        <div>
          <strong>Could not run the analysis</strong>
          <p>{error.message}</p>
        </div>
      </div>
      <div className="form-actions">
        <button type="button" className="btn" onClick={reset}>
          Try again
        </button>
        <Link className="btn btn-ghost" href="/investigations">
          Back to investigations
        </Link>
      </div>
    </div>
  );
}
