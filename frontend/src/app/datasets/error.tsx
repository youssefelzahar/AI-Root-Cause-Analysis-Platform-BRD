"use client";

export default function DatasetsError({
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
          <strong>Could not load datasets</strong>
          <p>{error.message}</p>
        </div>
      </div>
      <button type="button" className="btn" onClick={reset}>
        Retry
      </button>
    </div>
  );
}
