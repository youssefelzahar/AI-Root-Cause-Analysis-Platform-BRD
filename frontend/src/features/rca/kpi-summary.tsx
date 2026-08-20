import { DefinitionList, StatTile } from "@/components/ui";
import { formatDate, formatPercent, formatStat } from "@/lib/format";
import type { KpiChange, Periods } from "@/types/rca";

const GRAIN_LABELS: Record<string, string> = {
  day: "Daily",
  week: "Weekly",
  month: "Monthly",
  quarter: "Quarterly",
  year: "Yearly",
  equal_span: "Split in half by date",
  custom: "Custom range",
};

const COMPARISON_LABELS: Record<string, string> = {
  previous_period: "Previous period",
  previous_month: "Previous month",
  previous_quarter: "Previous quarter",
  previous_year: "Previous year",
  custom: "Custom",
};

/** `end` is exclusive, so the last day shown is the day before it. */
function windowLabel(start: string, end: string): string {
  const lastDay = new Date(new Date(end).getTime() - 86_400_000);
  return `${formatDate(start).split(",")[0]} to ${formatDate(lastDay.toISOString()).split(",")[0]}`;
}

export function KpiSummary({ kpi, periods }: { kpi: KpiChange; periods: Periods | null }) {
  // Tone follows the direction of travel, not whether that is good news: the
  // engine has no idea whether a falling number is a win.
  const tone = kpi.direction === "down" ? "negative" : kpi.direction === "up" ? "positive" : "neutral";

  return (
    <>
      <section className="stats-grid">
        <StatTile
          label="Previous period"
          value={formatStat(kpi.previous_value)}
          hint={periods ? windowLabel(periods.previous.start, periods.previous.end) : undefined}
        />
        <StatTile
          label="Current period"
          value={formatStat(kpi.current_value)}
          hint={periods ? windowLabel(periods.current.start, periods.current.end) : undefined}
        />
        <StatTile label="Change" value={formatStat(kpi.absolute_change)} tone={tone} />
        <StatTile
          label="Percent change"
          value={kpi.percent_change === null ? "—" : formatPercent(kpi.percent_change)}
          tone={tone}
          hint={
            kpi.percent_change_undefined_reason === "zero_baseline"
              ? "the previous period was zero"
              : undefined
          }
        />
      </section>

      <p className="muted rca-tone-note">
        Colour shows the direction of the change, not whether it is good — a falling cost is an
        improvement.
      </p>

      {periods ? (
        <DefinitionList
          items={[
            {
              label: "Measure",
              value: (
                <span className="mono">
                  {kpi.aggregation}({kpi.column})
                </span>
              ),
            },
            { label: "Time column", value: <span className="mono">{kpi.time_column ?? "—"}</span> },
            {
              label: "Compared against",
              value: COMPARISON_LABELS[kpi.comparison] ?? kpi.comparison,
            },
            { label: "Period length", value: GRAIN_LABELS[periods.grain] ?? periods.grain },
            {
              label: "Rows compared",
              value: `${periods.previous.row_count.toLocaleString()} previous, ${periods.current.row_count.toLocaleString()} current`,
            },
          ]}
        />
      ) : null}
    </>
  );
}
