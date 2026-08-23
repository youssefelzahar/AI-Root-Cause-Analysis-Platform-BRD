"use client";

import { ConfirmDeleteButton } from "@/components/confirm-delete-button";
import { deleteInvestigation } from "@/lib/api/rca";

/**
 * Discard a dataset's investigation.
 *
 * This deletes the KPI definition the analysis is computed from, not any stored
 * investigation. The dataset and its profile stay; only its Analysis Ready
 * status goes, which is what drops it off this list. Wording says "KPI" rather
 * than "investigation" so nobody reads the button as deleting the data.
 *
 * Investigations are now persisted rows, so a future DELETE
 * /api/investigations/{id} would remove one snapshot and mean something quite
 * different from this. Worth renaming both when that arrives.
 */
export function DeleteInvestigationButton({
  datasetId,
  name,
  redirectTo,
  iconOnly = true,
}: {
  datasetId: string;
  name: string;
  redirectTo?: string;
  iconOnly?: boolean;
}) {
  return (
    <ConfirmDeleteButton
      confirmLabel="Remove KPI"
      iconOnly={iconOnly}
      label={`Remove the KPI for ${name}`}
      onDelete={() => deleteInvestigation(datasetId)}
      redirectTo={redirectTo}
    />
  );
}
