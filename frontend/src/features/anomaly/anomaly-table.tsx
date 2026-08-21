import { Pill } from "@/components/ui";
import { formatNumber } from "@/lib/format";
import type { AnomalyObservation, AnomalySeverity } from "@/types/anomaly";

/**
 * The flagged periods as text.
 *
 * This is also the accessible rendering of the chart: the SVG is decorative
 * detail on top of these numbers, not the only place they appear.
 */

const SEVERITY_TONE: Record<AnomalySeverity, "neutral" | "info" | "warning" | "danger"> = {
  NORMAL: "neutral",
  LOW: "info",
  MEDIUM: "warning",
  HIGH: "warning",
  CRITICAL: "danger",
};

const DIRECTION_LABEL = {
  UPWARD: "Above baseline",
  DOWNWARD: "Below baseline",
  NONE: "On baseline",
} as const;

function deviation(observation: AnomalyObservation): string {
  if (observation.percentage_deviation === null) {
    return observation.percentage_unavailable_reason === "zero_baseline"
      ? "n/a (zero baseline)"
      : "-";
  }
  const sign = observation.percentage_deviation > 0 ? "+" : "";
  return `${sign}${observation.percentage_deviation.toFixed(1)}%`;
}

export function AnomalyTable({
  observations,
  emptyMessage,
}: {
  observations: AnomalyObservation[];
  emptyMessage: string;
}) {
  if (observations.length === 0) {
    return <p className="muted">{emptyMessage}</p>;
  }

  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Period</th>
            <th className="numeric">Actual</th>
            <th className="numeric">Expected</th>
            <th className="numeric">Deviation</th>
            <th className="numeric">Score</th>
            <th>Direction</th>
            <th>Severity</th>
          </tr>
        </thead>
        <tbody>
          {observations.map((observation) => (
            <tr key={observation.period_start}>
              <td>{observation.period_start.slice(0, 10)}</td>
              <td className="numeric">
                {observation.value === null ? "-" : formatNumber(observation.value)}
              </td>
              <td className="numeric">
                {observation.baseline
                  ? formatNumber(observation.baseline.expected_value)
                  : "-"}
              </td>
              <td className="numeric">{deviation(observation)}</td>
              <td className="numeric mono">
                {observation.anomaly_score === null
                  ? "-"
                  : observation.anomaly_score.toFixed(2)}
              </td>
              <td className="muted">{DIRECTION_LABEL[observation.direction]}</td>
              <td>
                <Pill tone={SEVERITY_TONE[observation.severity]}>{observation.severity}</Pill>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
