import { PageHeader } from "@/components/layout/page-header";
import { ConnectionForm } from "@/features/sql-editor/connection-form";

export default function NewConnectionPage() {
  return (
    <div>
      <PageHeader
        eyebrow="SQL Server"
        title="New connection"
        description="Credentials are encrypted before they are stored and are never returned by the API."
        backHref="/sql"
        backLabel="Connections"
      />
      <ConnectionForm />
    </div>
  );
}
