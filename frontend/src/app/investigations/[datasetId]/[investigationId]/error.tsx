"use client";

import { RefreshCw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { InvestigationError } from "@/features/rca/investigation-error";

export default function InvestigationPermalinkError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  // error.tsx receives no params, and the datasetId is kept in the URL precisely
  // so a re-run is one click from here.
  const params = useParams<{ datasetId: string }>();

  return (
    <InvestigationError
      title="Could not load this investigation"
      message={error.message}
      reset={reset}
      actions={
        params?.datasetId ? (
          <Link
            className="btn btn-secondary"
            href={`/investigations/${params.datasetId}`}
            prefetch={false}
          >
            <RefreshCw size={16} /> Run it again
          </Link>
        ) : null
      }
    />
  );
}
