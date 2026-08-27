"use client";

import Link from "next/link";

/**
 * The last resort.
 *
 * Deliberately thin, for the reason its siblings give: Next.js sanitises
 * server-component errors in production, so the API's error code does not survive
 * this boundary. Every failure a reader can act on is handled in the page or the
 * island instead, in `features/ai-analyst/analyst-blocked.tsx`. What lands here is
 * a genuine fault.
 */
export default function AiAnalystError({
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
          <strong>Could not open the analyst</strong>
          <p>{error.message}</p>
        </div>
      </div>
      <div className="form-actions">
        <button type="button" className="btn" onClick={reset}>
          Try again
        </button>
        <Link className="btn btn-ghost" href="/ai-analyst">
          Back to datasets
        </Link>
      </div>
    </div>
  );
}
