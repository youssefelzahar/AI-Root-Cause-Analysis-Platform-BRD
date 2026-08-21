import { DefinitionList, StatTile } from "@/components/ui";
import { formatNumber } from "@/lib/format";
import type { AnomalyMethod, AnomalyObservation, DetectionResult } from "@/types/anomaly";

/**
 * The latest period's verdict, plus how it was arrived at.
 *
 * Tone follows the *direction of travel*, not good-versus-bad: a KPI can be
 * unusually high because something went right, and the platform is not in a
 * position to know which. The same convention `kpi-summary.tsx` uses for RCA.
 */

function tone(observation: AnomalyObservation): "neutral" | "positive" | "negative" {
  if (!observation.is_anomaly) return "neutral";
  if (observation.direction === "UPWARD") return "positive";
  if (observation.direction === "DOWNWARD") return "negative";
  return "neutral";
}

function value(observation: AnomalyObservation): string {
  return observation.value === null ? "No data" : formatNumber(observation.value);
}

function deviation(observation: AnomalyObservation): string {
  if (observation.percentage_deviation !== null) {
    const sign = observation.percentage_deviation > 0 ? "+" : "";
    return `${sign}${observation.percentage_deviation.toFixed(1)}%`;
  }
  if (observation.absolute_deviation !== null) {
    const sign = observation.absolute_deviation > 0 ? "+" : "";
    return `${sign}${formatNumber(observation.absolute_deviation)}`;
  }
  return "-";
}

function deviationHint(observation: AnomalyObservation): string {
  if (observation.percentage_unavailable_reason === "zero_baseline") {
    return "No percentage: the baseline is zero";
  }
  return "Against the baseline";
}

export function AnomalySummary({
  latest,
  method,
  kpi,
  evidence,
}: {
  latest: AnomalyObservation;
  method: AnomalyMethod;
  kpi: DetectionResult["kpi"];
  evidence: DetectionResult["evidence"];
}) {
  const score = latest.anomaly_score;

  return (
    <>
      <section className="stats-grid">
        <StatTile
          label={`Latest ${kpi.grain}`}
          value={value(latest)}
          hint={latest.period_start.slice(0, 10)}
        />
        <StatTile
          label="Expected"
          value={
            latest.baseline ? formatNumber(latest.baseline.expected_value) : "Not established"
          }
          hint={
            latest.baseline
              ? `Median of the previous ${latest.baseline.observations_used} ${kpi.grain}s`
              : "Too little history"
          }
        />
        <StatTile
          label="Deviation"
          value={deviation(latest)}
          hint={deviationHint(latest)}
          tone={tone(latest)}
        />
        <StatTile
          label="Status"
          value={latest.is_anomaly ? `${latest.severity} anomaly` : latest.severity}
          hint={score === null ? "Not scored" : `Score ${score.toFixed(2)}`}
          tone={tone(latest)}
        />
      </section>

      <DefinitionList
        items={[
          { label: "KPI", value: `${kpi.aggregation} of ${kpi.column}` },
          { label: "Time column", value: kpi.time_column ?? "-" },
          { label: "Reporting grain", value: kpi.grain },
          { label: "Method", value: method.name },
          {
            label: "Baseline",
            value: `Trailing ${method.baseline_window} ${kpi.grain}s, at least ${method.min_baseline_observations}`,
          },
          {
            label: "Threshold",
            value: (
              <span className="mono">{`|score| >= ${method.anomaly_threshold}`}</span>
            ),
          },
          {
            label: "Periods",
            value: `${formatNumber(evidence.periods_observed)} observed, ${formatNumber(
              evidence.periods_evaluated,
            )} judged, ${formatNumber(evidence.periods_missing)} missing`,
          },
          { label: "Score means", value: method.score_interpretation },
        ]}
      />
    </>
  );
}
