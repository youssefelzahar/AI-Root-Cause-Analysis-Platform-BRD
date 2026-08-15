"use client";

import { Play, Save } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Alert, Panel, PanelHeading } from "@/components/ui";
import { toUserMessage } from "@/lib/api/errors";
import { executeQuery, saveQueryAsDataset } from "@/lib/api/sql";
import { cn, formatCell, formatNumber } from "@/lib/format";
import type { SqlExecuteResult } from "@/types/api";

const DEFAULT_ROW_LIMIT = 500;
const INITIAL_RENDER_ROWS = 200;

/** Advisory only - read-only enforcement is the backend's job. */
function looksReadOnly(sql: string): boolean {
  const first = sql.trim().replace(/^--.*$/gm, "").trim().split(/\s+/)[0]?.toUpperCase();
  return first === "SELECT" || first === "WITH" || first === "";
}

export function SqlWorkspace({ connectionId }: { connectionId: string }) {
  const router = useRouter();
  const [sql, setSql] = useState("SELECT TOP 100 * FROM ");
  const [result, setResult] = useState<SqlExecuteResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAllRows, setShowAllRows] = useState(false);

  const [datasetName, setDatasetName] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    setShowAllRows(false);
    try {
      setResult(await executeQuery(connectionId, sql, DEFAULT_ROW_LIMIT));
    } catch (runError) {
      setResult(null);
      setError(toUserMessage(runError));
    } finally {
      setRunning(false);
    }
  };

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const dataset = await saveQueryAsDataset(connectionId, {
        sql,
        dataset_name: datasetName.trim(),
      });
      // Lands on the detail page, where the status watcher takes over.
      router.push(`/datasets/${dataset.id}`);
    } catch (error) {
      setSaveError(toUserMessage(error));
    } finally {
      setSaving(false);
    }
  };

  const visibleRows = result
    ? showAllRows
      ? result.rows
      : result.rows.slice(0, INITIAL_RENDER_ROWS)
    : [];

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Panel>
        <PanelHeading eyebrow="Query" title="SQL editor" />
        <textarea
          className="textarea textarea-code"
          value={sql}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          aria-label="SQL query"
          onChange={(event) => setSql(event.target.value)}
          onKeyDown={(event) => {
            // Ctrl/Cmd+Enter runs - the one keybinding analysts expect.
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
              event.preventDefault();
              void run();
            }
          }}
        />

        {!looksReadOnly(sql) ? (
          <Alert tone="warning" title="This does not look like a SELECT">
            Only single read-only SELECT statements can be executed. The server will reject
            anything else.
          </Alert>
        ) : null}

        <div className="form-actions" style={{ marginTop: 12 }}>
          <button type="button" className="btn" onClick={run} disabled={running || !sql.trim()}>
            <Play size={16} />
            {running ? "Running…" : "Run query"}
          </button>
          <span className="muted" style={{ alignSelf: "center", fontSize: 12 }}>
            Ctrl/⌘ + Enter
          </span>
        </div>

        {error ? (
          <div style={{ marginTop: 16 }}>
            <Alert tone="danger" title="Query failed">
              {error}
            </Alert>
          </div>
        ) : null}
      </Panel>

      {result ? (
        <Panel>
          <PanelHeading
            eyebrow="Result"
            title={`${formatNumber(result.row_count)} rows · ${result.elapsed_ms} ms`}
          />

          {result.truncated ? (
            <Alert tone="info" title="Results truncated">
              Showing the first {formatNumber(DEFAULT_ROW_LIMIT)} rows. Refine the query with TOP or
              WHERE to narrow the result.
            </Alert>
          ) : null}

          {result.rows.length === 0 ? (
            <p className="muted">The query succeeded and returned no rows.</p>
          ) : (
            <>
              <div className="result-grid-wrap">
                <table className="result-grid">
                  <thead>
                    <tr>
                      <th className="row-number">#</th>
                      {result.columns.map((column) => (
                        <th key={column.name} title={column.name}>
                          {column.name}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {visibleRows.map((row, rowIndex) => (
                      <tr key={rowIndex}>
                        <td className="row-number">{rowIndex + 1}</td>
                        {row.map((value, cellIndex) => {
                          const cell = formatCell(value);
                          return (
                            <td
                              key={cellIndex}
                              className={cn(cell.isNull && "cell-null")}
                              title={cell.text}
                            >
                              {cell.text}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {!showAllRows && result.rows.length > INITIAL_RENDER_ROWS ? (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  style={{ marginTop: 10 }}
                  onClick={() => setShowAllRows(true)}
                >
                  Show all {formatNumber(result.rows.length)} rows
                </button>
              ) : null}
            </>
          )}
        </Panel>
      ) : null}

      {result && result.rows.length > 0 ? (
        <Panel>
          <PanelHeading eyebrow="Save" title="Save output as a dataset" />
          <p className="field-hint">
            The query result is stored as an internal dataset and profiled like an uploaded file.
          </p>
          <div className="form-row" style={{ marginTop: 12 }}>
            <div className="field">
              <label className="field-label" htmlFor="dataset-name">
                Dataset name
              </label>
              <input
                id="dataset-name"
                className="input"
                value={datasetName}
                onChange={(event) => setDatasetName(event.target.value)}
              />
            </div>
          </div>
          {saveError ? (
            <Alert tone="danger" title="Could not save">
              {saveError}
            </Alert>
          ) : null}
          <div className="form-actions">
            <button
              type="button"
              className="btn"
              onClick={save}
              disabled={saving || !datasetName.trim()}
            >
              <Save size={16} />
              {saving ? "Saving…" : "Save as dataset"}
            </button>
          </div>
        </Panel>
      ) : null}
    </div>
  );
}
