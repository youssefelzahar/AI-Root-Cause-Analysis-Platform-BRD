"use client";

import { useCallback, useState } from "react";

import { Alert, Badge, Panel, PanelHeading, StatTile, ValidationPill } from "@/components/ui";
import { formatBytes, formatNumber, formatPercent, formatStat } from "@/lib/format";
import type {
  ColumnProfile,
  DatasetProfile,
  SchemaValidation,
  ValidationState,
} from "@/types/api";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "columns", label: "Columns" },
  { id: "quality", label: "Quality" },
  { id: "statistics", label: "Statistics" },
] as const;

type TabId = (typeof TABS)[number]["id"];

function isTabId(value: string | null): value is TabId {
  return TABS.some((tab) => tab.id === value);
}

export function ProfileTabs({
  profile,
  columns,
  validation,
  initialTab,
}: {
  profile: DatasetProfile;
  columns: ColumnProfile[];
  validation: SchemaValidation | null;
  initialTab: string | null;
}) {
  const [active, setActive] = useState<TabId>(isTabId(initialTab) ? initialTab : "overview");

  // Sync the tab into the URL without a server round trip, so the view is
  // still shareable and survives a refresh.
  const selectTab = useCallback((tab: TabId) => {
    setActive(tab);
    if (typeof window !== "undefined") {
      window.history.replaceState(null, "", `?tab=${tab}`);
    }
  }, []);

  return (
    <div>
      <div className="tablist" role="tablist" aria-label="Data profile sections">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`tab-${tab.id}`}
            aria-selected={active === tab.id}
            aria-controls={`panel-${tab.id}`}
            className="tab"
            onClick={() => selectTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${active}`} aria-labelledby={`tab-${active}`}>
        {active === "overview" ? <OverviewTab profile={profile} columns={columns} /> : null}
        {active === "columns" ? <ColumnsTab columns={columns} /> : null}
        {active === "quality" ? <QualityTab validation={validation} columns={columns} /> : null}
        {active === "statistics" ? <StatisticsTab columns={columns} /> : null}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- overview */
function OverviewTab({
  profile,
  columns,
}: {
  profile: DatasetProfile;
  columns: ColumnProfile[];
}) {
  const byType = columns.reduce<Record<string, number>>((acc, column) => {
    acc[column.inferred_type] = (acc[column.inferred_type] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div>
      <section className="stats-grid">
        <StatTile label="Rows" value={formatNumber(profile.row_count)} />
        <StatTile label="Columns" value={formatNumber(profile.column_count)} />
        <StatTile label="File size" value={formatBytes(profile.file_size_bytes)} />
        <StatTile
          label="Duplicate rows"
          value={
            profile.duplicate_check_skipped ? "Skipped" : formatNumber(profile.duplicate_row_count)
          }
          hint={
            profile.duplicate_check_skipped
              ? "Too many columns to check"
              : formatPercent(profile.duplicate_row_pct ?? 0)
          }
          tone={(profile.duplicate_row_pct ?? 0) > 30 ? "negative" : "neutral"}
        />
        <StatTile
          label="Missing cells"
          value={formatPercent(profile.missing_cell_pct)}
          tone={profile.missing_cell_pct > 40 ? "negative" : "neutral"}
        />
        <StatTile
          label="Dataset status"
          value={(profile.quality_status ?? "unknown").toUpperCase()}
          tone={
            profile.quality_status === "blocked"
              ? "negative"
              : profile.quality_status === "pass"
                ? "positive"
                : "neutral"
          }
        />
      </section>

      <Panel>
        <PanelHeading eyebrow="Composition" title="Column types" />
        <div className="chip-row">
          {Object.entries(byType).map(([type, count]) => (
            <span className="chip" key={type}>
              {type}: {count}
            </span>
          ))}
        </div>
        {!profile.exact_quantiles ? (
          <p className="muted" style={{ marginTop: 16, fontSize: 13 }}>
            Percentiles are approximate on this dataset because of its size.
          </p>
        ) : null}
      </Panel>
    </div>
  );
}

/* ---------------------------------------------------------------- columns */
function ColumnsTab({ columns }: { columns: ColumnProfile[] }) {
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");

  const types = Array.from(new Set(columns.map((column) => column.inferred_type)));
  const filtered = columns.filter((column) => {
    const matchesSearch = column.column_name.toLowerCase().includes(search.toLowerCase());
    const matchesType = typeFilter === "all" || column.inferred_type === typeFilter;
    return matchesSearch && matchesType;
  });

  return (
    <Panel>
      <div className="toolbar">
        <input
          className="input"
          style={{ maxWidth: 260 }}
          placeholder="Search columns"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          aria-label="Search columns"
        />
        <select
          className="select"
          style={{ maxWidth: 180 }}
          value={typeFilter}
          onChange={(event) => setTypeFilter(event.target.value)}
          aria-label="Filter by type"
        >
          <option value="all">All types</option>
          {types.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
      </div>

      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Column</th>
              <th>Type</th>
              <th>Role</th>
              <th className="numeric">Missing</th>
              <th className="numeric">Unique</th>
              <th>Min</th>
              <th>Max</th>
              <th>Conversion</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((column) => (
              <tr key={column.column_name}>
                <td>
                  <strong>{column.column_name}</strong>
                </td>
                <td>
                  <Badge>{column.inferred_type}</Badge>
                </td>
                <td className="muted">{column.semantic_type}</td>
                <td className="numeric">{formatPercent(column.null_pct)}</td>
                <td className="numeric">{formatNumber(column.unique_count)}</td>
                <td className="muted">{column.min_value ?? "—"}</td>
                <td className="muted">{column.max_value ?? "—"}</td>
                <td>
                  {column.requires_conversion ? (
                    <span
                      className={
                        (column.conversion_confidence ?? 0) >= 0.99 ? "positive-text" : "negative-text"
                      }
                    >
                      {formatPercent((column.conversion_confidence ?? 0) * 100, 0)}
                    </span>
                  ) : (
                    <span className="muted">native</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {filtered.length === 0 ? <p className="muted">No columns match that filter.</p> : null}
    </Panel>
  );
}

/* ---------------------------------------------------------------- quality */
function QualityTab({
  validation,
  columns,
}: {
  validation: SchemaValidation | null;
  columns: ColumnProfile[];
}) {
  if (!validation) {
    return (
      <Panel>
        <p className="muted">Validation has not run for this dataset yet.</p>
      </Panel>
    );
  }

  const tone: Record<ValidationState, "success" | "warning" | "danger"> = {
    pass: "success",
    warning: "warning",
    blocked: "danger",
  };

  const convertible = columns.filter((column) => column.requires_conversion);

  return (
    <div>
      <Alert
        tone={tone[validation.state]}
        title={`Schema validation: ${validation.state.toUpperCase()}`}
      >
        {validation.state === "pass"
          ? "This dataset is structurally valid for analysis."
          : validation.state === "warning"
            ? "This dataset can be analysed but has quality issues."
            : "This dataset cannot safely be used for root cause analysis."}
      </Alert>

      <Panel>
        <PanelHeading
          eyebrow="Findings"
          title={`${validation.error_count} errors · ${validation.warning_count} warnings · ${validation.info_count} info`}
          actions={<ValidationPill state={validation.state} />}
        />
        {validation.issues.length === 0 ? (
          <p className="muted">No issues were found.</p>
        ) : (
          <ul className="issue-list">
            {validation.issues.map((issue, index) => (
              <li className={`issue ${issue.severity}`} key={`${issue.code}-${index}`}>
                <p className="issue-code">
                  {issue.code}
                  {issue.column ? ` · ${issue.column}` : ""}
                </p>
                <p>{issue.message}</p>
                {Array.isArray(issue.details?.sample_invalid_values) &&
                issue.details.sample_invalid_values.length ? (
                  <p className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                    Examples that could not be converted:{" "}
                    <span className="mono">
                      {(issue.details.sample_invalid_values as string[]).join(", ")}
                    </span>
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {convertible.length ? (
        <Panel className="panel" >
          <PanelHeading eyebrow="Type inference" title="Columns converted from text" />
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Detected as</th>
                  <th>Converted to</th>
                  <th className="numeric">Confidence</th>
                  <th className="numeric">Invalid values</th>
                </tr>
              </thead>
              <tbody>
                {convertible.map((column) => (
                  <tr key={column.column_name}>
                    <td>
                      <strong>{column.column_name}</strong>
                    </td>
                    <td className="muted">{column.raw_type}</td>
                    <td>
                      <Badge>{column.inferred_type}</Badge>
                    </td>
                    <td className="numeric">
                      {formatPercent((column.conversion_confidence ?? 0) * 100, 1)}
                    </td>
                    <td className="numeric">{formatNumber(column.invalid_value_count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------- statistics */
function StatisticsTab({ columns }: { columns: ColumnProfile[] }) {
  const numeric = columns.filter((c) => c.inferred_type === "numeric" || c.inferred_type === "integer");
  const categorical = columns.filter((c) => c.inferred_type === "string" || c.inferred_type === "boolean");
  const temporal = columns.filter((c) => c.inferred_type === "date" || c.inferred_type === "datetime");

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {numeric.length ? (
        <Panel>
          <PanelHeading eyebrow="Numeric" title="Distribution and outliers" />
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Column</th>
                  <th className="numeric">Min</th>
                  <th className="numeric">Max</th>
                  <th className="numeric">Mean</th>
                  <th className="numeric">Median</th>
                  <th className="numeric">Std dev</th>
                  <th className="numeric">P25</th>
                  <th className="numeric">P75</th>
                  <th className="numeric">P95</th>
                  <th className="numeric">Outliers</th>
                </tr>
              </thead>
              <tbody>
                {numeric.map((column) => (
                  <tr key={column.column_name}>
                    <td>
                      <strong>{column.column_name}</strong>
                    </td>
                    <td className="numeric">{column.min_value ?? "—"}</td>
                    <td className="numeric">{column.max_value ?? "—"}</td>
                    <td className="numeric">{formatStat(column.mean)}</td>
                    <td className="numeric">{formatStat(column.median)}</td>
                    <td className="numeric">{formatStat(column.stddev)}</td>
                    <td className="numeric">{formatStat(column.percentiles?.p25)}</td>
                    <td className="numeric">{formatStat(column.percentiles?.p75)}</td>
                    <td className="numeric">{formatStat(column.percentiles?.p95)}</td>
                    <td className="numeric">{formatNumber(column.outlier_count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
            Outliers are counted outside the 1.5 × IQR fences.
          </p>
        </Panel>
      ) : null}

      {categorical.length ? (
        <Panel>
          <PanelHeading eyebrow="Categorical" title="Most frequent values" />
          <div style={{ display: "grid", gap: 16 }}>
            {categorical.map((column) => (
              <div key={column.column_name}>
                <div className="toolbar" style={{ marginBottom: 6 }}>
                  <strong>{column.column_name}</strong>
                  <span className="muted">
                    {formatNumber(column.unique_count)} distinct · {formatPercent(column.null_pct)}{" "}
                    missing
                  </span>
                </div>
                <div className="table-wrap">
                  <table className="data-table">
                    <tbody>
                      {(column.top_values ?? []).slice(0, 8).map((item) => (
                        <tr key={item.value}>
                          <td style={{ width: "40%" }}>{item.value}</td>
                          <td className="numeric" style={{ width: 90 }}>
                            {formatNumber(item.count)}
                          </td>
                          <td className="numeric" style={{ width: 70 }}>
                            {formatPercent(item.pct ?? 0)}
                          </td>
                          <td>
                            <div className="freq-bar">
                              <span style={{ width: `${item.pct ?? 0}%` }} />
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      ) : null}

      {temporal.length ? (
        <Panel>
          <PanelHeading eyebrow="Datetime" title="Coverage" />
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Min date</th>
                  <th>Max date</th>
                  <th className="numeric">Periods</th>
                  <th>Frequency</th>
                  <th className="numeric">Missing periods</th>
                </tr>
              </thead>
              <tbody>
                {temporal.map((column) => (
                  <tr key={column.column_name}>
                    <td>
                      <strong>{column.column_name}</strong>
                    </td>
                    <td>{column.datetime_stats?.min_date ?? "—"}</td>
                    <td>{column.datetime_stats?.max_date ?? "—"}</td>
                    <td className="numeric">
                      {formatNumber(column.datetime_stats?.distinct_periods)}
                    </td>
                    <td>
                      {column.datetime_stats?.detected_frequency ? (
                        <Badge>{column.datetime_stats.detected_frequency}</Badge>
                      ) : (
                        <span className="muted">irregular</span>
                      )}
                    </td>
                    <td className="numeric">
                      {column.datetime_stats?.missing_periods === null ||
                      column.datetime_stats?.missing_periods === undefined
                        ? "—"
                        : formatNumber(column.datetime_stats.missing_periods)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
            Missing periods are only reported when a frequency could be detected confidently.
          </p>
        </Panel>
      ) : null}
    </div>
  );
}
