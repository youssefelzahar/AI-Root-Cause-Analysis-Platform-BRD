"use client";

import { ConfirmDeleteButton } from "@/components/confirm-delete-button";
import { deleteConnection } from "@/lib/api/sql";

/**
 * Delete a saved SQL Server connection.
 *
 * A thin wrapper over the shared control, like its siblings for datasets and
 * investigations - the two-step inline confirm, the error handling and the
 * `router.refresh()` all live there.
 *
 * Nothing is blocked: unlike a dataset mid-profile, a connection has no pipeline
 * state that a delete could interrupt. Datasets already saved from its queries are
 * unaffected - the rows were materialised to Parquet at save time, so they do not
 * read back through the connection.
 */
export function DeleteConnectionButton({
  connectionId,
  name,
  iconOnly = true,
  redirectTo,
}: {
  connectionId: string;
  name: string;
  iconOnly?: boolean;
  /** Set on the detail page, where deleting the thing being viewed must navigate. */
  redirectTo?: string;
}) {
  return (
    <ConfirmDeleteButton
      confirmLabel="Delete connection"
      iconOnly={iconOnly}
      label={`Delete ${name}`}
      onDelete={() => deleteConnection(connectionId)}
      redirectTo={redirectTo}
    />
  );
}
