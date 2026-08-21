"use client";

import Link from "next/link";

/**
 * The last resort for a failed detection.
 *
 * Deliberately thin: Next.js sanitises server-component errors in production,
 * so the API's error code does not survive this boundary. Every failure a user
 * can act on is therefore handled in the page itself, in
 * `features/anomaly/anomaly-blocked.tsx`. What lands here is a genuine fault.
 */
export default function AnomalyError({
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
          <strong>Could not check for anomalies</strong>
          <p>{error.message}</p>
        </div>
      </div>
      <div className="form-actions">
        <button type="button" className="btn" onClick={reset}>
          Try again
        </button>
        <Link className="btn btn-ghost" href="/anomalies">
          Back to anomalies
        </Link>
      </div>
    </div>
  );
}
