"use client";

import { PlayCircle } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Alert, Badge, Panel, PanelHeading } from "@/components/ui";
import { createKpiDefinition } from "@/lib/api/datasets";
import { toUserMessage } from "@/lib/api/errors";
import { cn, formatNumber } from "@/lib/format";
import type {
  ColumnProfile,
  KpiCandidate,
  KpiCandidates,
  KpiDefinition,
  ValidationIssue,
} from "@/types/api";

import {
  AGGREGATIONS,
  COMPARISONS,
  describe,
  type KpiFormState,
  toPayload,
  validateKpiForm,
} from "./validate-kpi";

export function KpiSetupForm({
  datasetId,
  candidates,
  columns,
  existing,
}: {
  datasetId: string;
  candidates: KpiCandidates;
  columns: ColumnProfile[];
  existing: KpiDefinition | null;
}) {
  const router = useRouter();
  const suggested = candidates.recommended_default;

  const [state, setState] = useState<KpiFormState>({
    name: existing?.name ?? suggested.name ?? "",
    column: existing?.column_name ?? suggested.column ?? "",
    aggregation: existing?.aggregation ?? suggested.aggregation ?? "SUM",
    time_column: existing?.time_column ?? suggested.time_column ?? "",
    dimensions: existing?.dimensions ?? suggested.dimensions ?? [],
    comparison: existing?.comparison ?? suggested.comparison ?? "previous_period",
  });

  const [showAllColumns, setShowAllColumns] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [serverIssues, setServerIssues] = useState<ValidationIssue[]>([]);
  const [saved, setSaved] = useState(false);

  const errors = useMemo(() => validateKpiForm(state, candidates), [state, candidates]);
  const payload = toPayload(state);

  const update = <K extends keyof KpiFormState>(key: K, value: KpiFormState[K]) => {
    setState((current) => ({ ...current, [key]: value }));
    setSaved(false);
  };

  const toggleDimension = (column: string) => {
    setState((current) => ({
      ...current,
      dimensions: current.dimensions.includes(column)
        ? current.dimensions.filter((item) => item !== column)
        : [...current.dimensions, column],
    }));
    setSaved(false);
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setServerError(null);
    setServerIssues([]);

    // The button stays enabled and explains what is wrong, rather than being
    // silently disabled with no reason.
    if (Object.keys(errors).length > 0) return;

    setSaving(true);
    try {
      await createKpiDefinition(datasetId, payload);
      setSaved(true);
      router.refresh();
    } catch (error) {
      setServerError(toUserMessage(error));
      const details = (error as { details?: { issues?: ValidationIssue[] } })?.details;
      if (details?.issues) setServerIssues(details.issues);
    } finally {
      setSaving(false);
    }
  };

  const measureOptions = showAllColumns
    ? columns.map<KpiCandidate>((column) => ({
        column: column.column_name,
        score: column.kpi_measure_score ?? 0,
        reasons: [`${column.inferred_type} column`],
        suggested_aggregation: column.suggested_aggregation,
        dtype: column.inferred_type,
        cardinality: column.unique_count,
        detected_frequency: null,
        min_date: null,
        max_date: null,
        distinct_periods: null,
      }))
    : candidates.measures;

  return (
    <form className="content-grid" onSubmit={handleSubmit} noValidate>
      <div style={{ display: "grid", gap: 16 }}>
        <Panel>
          <PanelHeading eyebrow="Step 1" title="Choose the KPI" />

          <div className="field">
            <label className="field-label" htmlFor="kpi-name">
              KPI name
            </label>
            <input
              id="kpi-name"
              className="input"
              value={state.name}
              onChange={(event) => update("name", event.target.value)}
              aria-invalid={submitted && Boolean(errors.name)}
            />
            {submitted && errors.name ? <p className="field-error">{errors.name}</p> : null}
          </div>

          <div className="field">
            <span className="field-label">Measure column</span>
            <p className="field-hint">
              Recommended candidates come from the data profile, with the reason they were chosen.
            </p>
            <div className="option-list">
              {measureOptions.slice(0, showAllColumns ? undefined : 5).map((candidate, index) => (
                <button
                  key={candidate.column}
                  type="button"
                  className={cn("option-card", state.column === candidate.column && "selected")}
                  onClick={() => {
                    update("column", candidate.column);
                    if (candidate.suggested_aggregation) {
                      update("aggregation", candidate.suggested_aggregation);
                    }
                  }}
                >
                  <input
                    type="radio"
                    checked={state.column === candidate.column}
                    readOnly
                    tabIndex={-1}
                    aria-hidden
                  />
                  <span className="option-body">
                    <span className="option-title">
                      {candidate.column}
                      {candidate.dtype ? <Badge>{candidate.dtype}</Badge> : null}
                      {index === 0 && !showAllColumns ? (
                        <Badge recommended>Recommended</Badge>
                      ) : null}
                    </span>
                    <span className="option-reason">{candidate.reasons.join(" · ")}</span>
                  </span>
                </button>
              ))}
            </div>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={showAllColumns}
                onChange={(event) => setShowAllColumns(event.target.checked)}
              />
              Show all columns (including text columns that convert to numbers)
            </label>
            {submitted && errors.column ? <p className="field-error">{errors.column}</p> : null}
          </div>

          <div className="form-row">
            <div className="field">
              <label className="field-label" htmlFor="kpi-aggregation">
                Aggregation
              </label>
              <select
                id="kpi-aggregation"
                className="select"
                value={state.aggregation}
                onChange={(event) =>
                  update("aggregation", event.target.value as KpiFormState["aggregation"])
                }
              >
                {AGGREGATIONS.map((aggregation) => (
                  <option key={aggregation} value={aggregation}>
                    {aggregation}
                  </option>
                ))}
              </select>
            </div>

            <div className="field">
              <label className="field-label" htmlFor="kpi-comparison">
                Comparison period
              </label>
              <select
                id="kpi-comparison"
                className="select"
                value={state.comparison}
                onChange={(event) =>
                  update("comparison", event.target.value as KpiFormState["comparison"])
                }
              >
                {COMPARISONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </Panel>

        <Panel>
          <PanelHeading eyebrow="Step 2" title="Time dimension (optional)" />
          <div className="option-list">
            {candidates.time_columns.map((candidate, index) => (
              <button
                key={candidate.column}
                type="button"
                className={cn("option-card", state.time_column === candidate.column && "selected")}
                onClick={() => update("time_column", candidate.column)}
              >
                <input
                  type="radio"
                  checked={state.time_column === candidate.column}
                  readOnly
                  tabIndex={-1}
                  aria-hidden
                />
                <span className="option-body">
                  <span className="option-title">
                    {candidate.column}
                    {candidate.detected_frequency ? (
                      <Badge>{candidate.detected_frequency}</Badge>
                    ) : null}
                    {index === 0 ? <Badge recommended>Recommended</Badge> : null}
                  </span>
                  <span className="option-reason">
                    {candidate.min_date && candidate.max_date
                      ? `${candidate.min_date} → ${candidate.max_date} · `
                      : ""}
                    {candidate.reasons.join(" · ")}
                  </span>
                </span>
              </button>
            ))}
          </div>
          {candidates.time_columns.length === 0 ? (
            <p className="muted">
              No date column was detected. You can still create the KPI from the available
              columns; period comparison will be skipped.
            </p>
          ) : null}
          {submitted && errors.time_column ? (
            <p className="field-error">{errors.time_column}</p>
          ) : null}
        </Panel>

        <Panel>
          <PanelHeading eyebrow="Step 3" title="Analysis dimensions" />
          <p className="field-hint">
            The RCA engine will drill into these to explain a change.
          </p>
          <div style={{ marginTop: 8 }}>
            {candidates.dimensions.map((candidate, index) => (
              <label className="checkbox-row" key={candidate.column}>
                <input
                  type="checkbox"
                  checked={state.dimensions.includes(candidate.column)}
                  onChange={() => toggleDimension(candidate.column)}
                />
                <span>
                  {candidate.column}{" "}
                  <span className="muted">
                    ({formatNumber(candidate.cardinality)} distinct)
                  </span>{" "}
                  {index < 3 ? <Badge recommended>Recommended</Badge> : null}
                </span>
              </label>
            ))}
          </div>
          {state.dimensions.length ? (
            <div className="chip-row">
              {state.dimensions.map((dimension) => (
                <span className="chip" key={dimension}>
                  {dimension}
                  <button
                    type="button"
                    onClick={() => toggleDimension(dimension)}
                    aria-label={`Remove ${dimension}`}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          ) : null}
          {submitted && errors.dimensions ? (
            <p className="field-error">{errors.dimensions}</p>
          ) : null}
        </Panel>
      </div>

      <div style={{ display: "grid", gap: 16, position: "sticky", top: 16 }}>
        <Panel>
          <PanelHeading eyebrow="Summary" title="KPI definition" />
          <p>{describe(state)}</p>

          <details style={{ marginTop: 16 }}>
            <summary className="muted" style={{ cursor: "pointer", fontSize: 13 }}>
              Definition JSON sent to the RCA engine
            </summary>
            <pre className="mono" style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>
              {JSON.stringify(payload, null, 2)}
            </pre>
          </details>

          {submitted && Object.keys(errors).length > 0 ? (
            <div style={{ marginTop: 16 }}>
              <Alert tone="warning" title="Fix these before starting the analysis">
                {Object.values(errors).join(" ")}
              </Alert>
            </div>
          ) : null}

          {serverError ? (
            <div style={{ marginTop: 16 }}>
              <Alert tone="danger" title="Could not save">
                {serverError}
              </Alert>
              {serverIssues.length ? (
                <ul className="issue-list">
                  {serverIssues.map((issue, index) => (
                    <li className={`issue ${issue.severity}`} key={`${issue.code}-${index}`}>
                      <p className="issue-code">{issue.code}</p>
                      <p>{issue.message}</p>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          {saved ? (
            <div style={{ marginTop: 16 }}>
              <Alert tone="success" title="Analysis Ready">
                The KPI definition is saved. Running RCA on your own dataset arrives in the next
                phase.
              </Alert>
            </div>
          ) : null}

          <div className="form-actions" style={{ marginTop: 16 }}>
            <button type="submit" className="btn" disabled={saving}>
              <PlayCircle size={16} />
              {saving ? "Saving…" : existing ? "Update KPI" : "Start analysis"}
            </button>
          </div>
        </Panel>
      </div>
    </form>
  );
}
