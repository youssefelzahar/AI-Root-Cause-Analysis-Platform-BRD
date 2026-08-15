import { Plus } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";
import { EmptyState, Panel, Pill } from "@/components/ui";
import { listConnections } from "@/lib/api/sql";
import { formatDate } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function SqlConnectionsPage() {
  const page = await listConnections();

  return (
    <div>
      <PageHeader
        eyebrow="Data foundation"
        title="SQL Server connections"
        description="Connect to SQL Server, run read-only queries, and save the output as a dataset."
        actions={
          <Link className="btn" href="/sql/new">
            <Plus size={16} /> New connection
          </Link>
        }
      />

      <Panel>
        {page.items.length === 0 ? (
          <EmptyState
            title="No connections yet"
            description="Add a SQL Server connection to run queries and save their output as datasets."
            action={
              <Link className="btn" href="/sql/new">
                Add a connection
              </Link>
            }
          />
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Server</th>
                  <th>Database</th>
                  <th>User</th>
                  <th>Last test</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {page.items.map((connection) => (
                  <tr key={connection.id}>
                    <td>
                      <Link href={`/sql/${connection.id}`}>{connection.name}</Link>
                    </td>
                    <td className="muted">
                      {connection.host}:{connection.port}
                    </td>
                    <td className="muted">{connection.database_name}</td>
                    <td className="muted">{connection.username}</td>
                    <td>
                      {connection.last_test_ok === null ? (
                        <Pill tone="neutral">Not tested</Pill>
                      ) : connection.last_test_ok ? (
                        <Pill tone="success">OK</Pill>
                      ) : (
                        <Pill tone="danger">Failed</Pill>
                      )}{" "}
                      <span className="muted">{formatDate(connection.last_tested_at)}</span>
                    </td>
                    <td>
                      <Link className="btn btn-ghost btn-sm" href={`/sql/${connection.id}`}>
                        Open editor
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
