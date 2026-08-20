"use client";

import { ConfirmDeleteButton } from "@/components/confirm-delete-button";
import { deleteDataset } from "@/lib/api/datasets";
import type { DatasetStatus } from "@/types/api";

/**
 * Delete a dataset, its stored file and everything profiled from it.
 *
 * Blocked while the backend is mid-pipeline: it answers 409 in that window, so
 * the button says why up front instead of surfacing the conflict as an error.
 */
export function DeleteDatasetButton({
  datasetId,
  name,
  status,
  redirectTo,
}: {
  datasetId: string;
  name: string;
  status: DatasetStatus;
  redirectTo?: string;
}) {
  const busy = status === "profiling" || status === "validating";

  return (
    <ConfirmDeleteButton
      confirmLabel="Delete dataset"
      disabled={busy}
      disabledReason="This dataset is still being processed."
      iconOnly
      label={`Delete ${name}`}
      onDelete={() => deleteDataset(datasetId)}
      redirectTo={redirectTo}
    />
  );
}
