"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Alert, Panel, PanelHeading } from "@/components/ui";
import { createConnection, testUnsavedConnection, type SqlConnectionInput } from "@/lib/api/sql";
import { toUserMessage } from "@/lib/api/errors";
import type { SqlConnectionTestResult } from "@/types/api";

const EMPTY: SqlConnectionInput = {
  name: "",
  host: "",
  port: 1433,
  database: "",
  username: "",
  password: "",
  encrypt: true,
  trust_server_certificate: false,
};

export function ConnectionForm() {
  const router = useRouter();
  const [form, setForm] = useState<SqlConnectionInput>(EMPTY);
  const [busy, setBusy] = useState<"test" | "save" | null>(null);
  const [testResult, setTestResult] = useState<SqlConnectionTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const update = <K extends keyof SqlConnectionInput>(key: K, value: SqlConnectionInput[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
    setTestResult(null);
  };

  const handleTest = async () => {
    setBusy("test");
    setError(null);
    try {
      // Sent in a POST body, never as a query parameter - a query string would
      // land in the nginx access log and browser history.
      setTestResult(await testUnsavedConnection(form));
    } catch (testError) {
      setError(toUserMessage(testError));
    } finally {
      setBusy(null);
    }
  };

  const handleSave = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy("save");
    setError(null);
    try {
      const connection = await createConnection(form);
      // Clear the password from component state as soon as it is stored.
      setForm({ ...EMPTY });
      router.push(`/sql/${connection.id}`);
    } catch (saveError) {
      setError(toUserMessage(saveError));
    } finally {
      setBusy(null);
    }
  };

  return (
    <form onSubmit={handleSave} autoComplete="off">
      <Panel>
        <PanelHeading eyebrow="SQL Server" title="Connection details" />

        <div className="field">
          <label className="field-label" htmlFor="conn-name">
            Connection name
          </label>
          <input
            id="conn-name"
            className="input"
            required
            value={form.name}
            onChange={(event) => update("name", event.target.value)}
          />
        </div>

        <div className="form-row">
          <div className="field">
            <label className="field-label" htmlFor="conn-host">
              Server / host
            </label>
            <input
              id="conn-host"
              className="input"
              required
              placeholder="sqlserver.internal"
              value={form.host}
              onChange={(event) => update("host", event.target.value)}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="conn-port">
              Port
            </label>
            <input
              id="conn-port"
              className="input"
              type="number"
              value={form.port}
              onChange={(event) => update("port", Number(event.target.value))}
            />
          </div>
        </div>

        <div className="field">
          <label className="field-label" htmlFor="conn-database">
            Database
          </label>
          <input
            id="conn-database"
            className="input"
            required
            value={form.database}
            onChange={(event) => update("database", event.target.value)}
          />
        </div>

        <div className="form-row">
          <div className="field">
            <label className="field-label" htmlFor="conn-username">
              Username
            </label>
            <input
              id="conn-username"
              className="input"
              required
              autoComplete="off"
              value={form.username}
              onChange={(event) => update("username", event.target.value)}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="conn-password">
              Password
            </label>
            <input
              id="conn-password"
              className="input"
              type="password"
              required
              // Not "password", so browsers do not offer to autofill the
              // user's own saved credentials into a database connection form.
              name="db-credential"
              autoComplete="new-password"
              value={form.password}
              onChange={(event) => update("password", event.target.value)}
            />
            <p className="field-hint">
              Encrypted before it is stored. It is never returned by the API.
            </p>
          </div>
        </div>

        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={form.encrypt}
            onChange={(event) => update("encrypt", event.target.checked)}
          />
          Encrypt the connection
        </label>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={form.trust_server_certificate}
            onChange={(event) => update("trust_server_certificate", event.target.checked)}
          />
          Trust the server certificate (needed for self-signed certificates)
        </label>

        {testResult ? (
          <div style={{ marginTop: 16 }}>
            <Alert
              tone={testResult.ok ? "success" : "danger"}
              title={testResult.ok ? "Connection succeeded" : "Connection failed"}
            >
              {testResult.ok
                ? `${testResult.server_version ?? "Connected"} · ${testResult.latency_ms ?? 0} ms`
                : (testResult.message ?? "Could not reach the server.")}
            </Alert>
          </div>
        ) : null}

        {error ? (
          <div style={{ marginTop: 16 }}>
            <Alert tone="danger" title="Error">
              {error}
            </Alert>
          </div>
        ) : null}

        <div className="form-actions" style={{ marginTop: 16 }}>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleTest}
            disabled={busy !== null || !form.host || !form.username}
          >
            {busy === "test" ? "Testing…" : "Test connection"}
          </button>
          <button type="submit" className="btn" disabled={busy !== null}>
            {busy === "save" ? "Saving…" : "Save connection"}
          </button>
        </div>
      </Panel>
    </form>
  );
}
