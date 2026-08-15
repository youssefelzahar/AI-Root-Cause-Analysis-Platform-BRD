import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { getConnection } from "@/lib/api/sql";
import { ApiError } from "@/lib/api/errors";
import { SqlWorkspace } from "@/features/sql-editor/sql-workspace";

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
        description={`${connection.host}:${connection.port} · ${connection.database_name}`}
        backHref="/sql"
        backLabel="Connections"
      />
      <SqlWorkspace connectionId={connectionId} />
    </div>
  );
}
