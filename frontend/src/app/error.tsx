"use client";

import { useEffect } from "react";

export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div>
      <div className="alert alert-danger" role="alert">
        <div>
          <strong>Something went wrong</strong>
          {/* Surfaced deliberately: the previous client swallowed API errors
              and rendered fallback data, so a dead backend looked healthy. */}
          <p>{error.message || "An unexpected error occurred."}</p>
        </div>
      </div>
      <button type="button" className="btn" onClick={reset}>
        Try again
      </button>
    </div>
  );
}
