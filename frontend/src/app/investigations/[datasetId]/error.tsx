"use client";

import { InvestigationError } from "@/features/rca/investigation-error";

export default function RunInvestigationError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <InvestigationError
      title="Could not run the analysis"
      message={error.message}
      reset={reset}
    />
  );
}
