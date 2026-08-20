"use client";

import { ConfirmDeleteButton } from "@/components/confirm-delete-button";
import { deleteInvestigation } from "@/lib/api/rca";

/**
 * Discard a dataset's investigation.
 *
 * There is no stored investigation to remove - the analysis is recomputed on
 * every request - so this deletes the KPI definition it is computed from. The
 * dataset and its profile stay; only its Analysis Ready status goes, which is
 * what drops it off this list. Wording says "KPI" rather than "investigation"
 * so nobody reads the button as deleting the data.
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
