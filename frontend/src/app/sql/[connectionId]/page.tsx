import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { DeleteConnectionButton } from "@/features/sql-editor/delete-connection-button";
import { SqlWorkspace } from "@/features/sql-editor/sql-workspace";
import { ApiError } from "@/lib/api/errors";
import { getConnection } from "@/lib/api/sql";

export const dynamic = "force-dynamic";

export default async function SqlEditorPage({
  params,
}: {
  params: Promise<{ connectionId: string }>;
}) {
  const { connectionId } = await params;

  let connection;
  try {
    connection = await getConnection(connectionId);
  } catch (error) {
    if (error instanceof ApiError && error.isNotFound) notFound();
    throw error;
  }

  return (
    <div>
      <PageHeader
        eyebrow="SQL editor"
        title={connection.name}
        description={`${connection.host}:${connection.port} · ${connection.database_name} · ${
          connection.auth_mode === "windows"
            ? "Windows authentication"
            : connection.username
        }`}
        backHref="/sql"
        backLabel="Connections"
        actions={
          <DeleteConnectionButton
            connectionId={connectionId}
            iconOnly={false}
            name={connection.name}
            // Deleting the connection being viewed has to leave the page: this
            // route would 404 on the refresh that follows.
            redirectTo="/sql"
          />
        }
      />
      <SqlWorkspace connectionId={connectionId} />
    </div>
  );
}
