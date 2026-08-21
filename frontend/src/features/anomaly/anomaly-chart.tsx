import type { AnomalyObservation } from "@/types/anomaly";

/**
 * The KPI series with its baseline band and the flagged periods.
 *
 * This is the first inline SVG in the app. `rca-tree.tsx` records why it stayed
 * with DOM and CSS, and that reasoning still holds for a tree or a bar: those
 * are lists of independent magnitudes, which CSS lays out natively. A *line* is
 * not - it is the connection between consecutive points, and a stack of absolute
 * divs cannot express one. So an SVG polyline earns its place here and nowhere
 * else yet.
 *
 * Three rules the chart must not break:
 *
 * * **The line breaks at gaps.** A period with no rows gets its own segment
 *   boundary. Drawing straight through it would invent the data the backend
 *   deliberately refused to invent.
 * * **Unscored periods look unscored.** On a seven-month series five points
 *   cannot be judged; rendering them as ordinary dots would claim five clean
 *   months that nothing supports.
 * * **Colour is never the only signal.** An anomaly gets a ring and a larger
 *   radius as well as a different fill.
 */

const WIDTH = 760;
const HEIGHT = 260;
const PAD = { top: 18, right: 16, bottom: 30, left: 56 };
const PLOT_W = WIDTH - PAD.left - PAD.right;
const PLOT_H = HEIGHT - PAD.top - PAD.bottom;

type Point = {
  x: number;
  y: number;
  observation: AnomalyObservation;
};

function niceValue(value: number): string {
  const magnitude = Math.abs(value);
  if (magnitude >= 1e9) return `${(value / 1e9).toFixed(1)}B`;
  if (magnitude >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (magnitude >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  if (magnitude >= 1) return value.toFixed(0);
  return value.toPrecision(2);
}

function periodLabel(iso: string): string {
  return iso.slice(0, 10);
}

/**
 * Geometry for the whole chart, in one pass.
 *
 * The vertical scale spans the values *and* the baseline band, so a band that
 * sits outside the observed range is still visible rather than clipped.
 */
function build(series: AnomalyObservation[], threshold: number) {
  const values: number[] = [];
  for (const o of series) {
    if (o.value !== null) values.push(o.value);
    if (o.baseline) {
      const reach = threshold * o.baseline.scale;
      values.push(o.baseline.expected_value - reach, o.baseline.expected_value + reach);
    }
  }

  const low = Math.min(...values);
  const high = Math.max(...values);
  // A flat series has zero range; give it one so every point does not land on
  // the same pixel row and division by zero cannot happen.
  const span = high - low || Math.max(Math.abs(high), 1);
  const top = high + span * 0.08;
  const bottom = low - span * 0.08;
  const range = top - bottom;

  const stepX = series.length > 1 ? PLOT_W / (series.length - 1) : 0;
  const x = (index: number) =>
    series.length > 1 ? PAD.left + index * stepX : PAD.left + PLOT_W / 2;
  const y = (value: number) => PAD.top + PLOT_H - ((value - bottom) / range) * PLOT_H;

  const points: Point[] = [];
  series.forEach((observation, index) => {
    if (observation.value === null) return;
    points.push({ x: x(index), y: y(observation.value), observation });
  });

  // One run per stretch of consecutive observed periods, so the line breaks
  // wherever the data does.
  const runs: Point[][] = [];
  let run: Point[] = [];
  series.forEach((observation, index) => {
    if (observation.value === null) {
      if (run.length) runs.push(run);
      run = [];
      return;
    }
    run.push({ x: x(index), y: y(observation.value), observation });
  });
  if (run.length) runs.push(run);

  // The band is only defined where a baseline exists, which is never the start
  // of the series. Built as its own runs for the same reason as the line.
  const bandRuns: { upper: string; lower: string }[] = [];
  let upper: string[] = [];
  let lower: string[] = [];
  const flushBand = () => {
    if (upper.length > 1) {
      bandRuns.push({ upper: upper.join(" "), lower: lower.reverse().join(" ") });
    }
    upper = [];
    lower = [];
  };
  series.forEach((observation, index) => {
    if (!observation.baseline) {
      flushBand();
      return;
    }
    const reach = threshold * observation.baseline.scale;
    upper.push(`${x(index)},${y(observation.baseline.expected_value + reach)}`);
    lower.push(`${x(index)},${y(observation.baseline.expected_value - reach)}`);
  });
  flushBand();

  const baselineRuns: string[] = [];
  let median: string[] = [];
  series.forEach((observation, index) => {
    if (!observation.baseline) {
      if (median.length > 1) baselineRuns.push(median.join(" "));
      median = [];
      return;
    }
    median.push(`${x(index)},${y(observation.baseline.expected_value)}`);
  });
  if (median.length > 1) baselineRuns.push(median.join(" "));

  return { points, runs, bandRuns, baselineRuns, bottom, top, y };
}

export function AnomalyChart({
  series,
  threshold,
  kpiName,
}: {
  series: AnomalyObservation[];
  threshold: number;
  kpiName: string;
}) {
  const observed = series.filter((o) => o.value !== null);
  if (observed.length === 0) return null;

  const { points, runs, bandRuns, baselineRuns, bottom, top, y } = build(series, threshold);
  const anomalies = points.filter((p) => p.observation.is_anomaly);
  const flagged = anomalies.length;

  return (
    <div className="anomaly-chart">
      <svg
        className="anomaly-chart-svg"
        role="img"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        aria-labelledby="anomaly-chart-title anomaly-chart-desc"
      >
        <title id="anomaly-chart-title">{kpiName} over time, with its baseline</title>
        <desc id="anomaly-chart-desc">
          {`${observed.length} periods, ${flagged} flagged as unusual. The shaded band is the `}
          {`baseline plus or minus the anomaly threshold. The table below carries the same numbers.`}
        </desc>

        {/* Horizontal guides at the ends of the scale, so a reader can size the
            movement without reading every label. */}
        {[bottom, (bottom + top) / 2, top].map((value) => (
          <g key={value}>
            <line
              className="anomaly-grid"
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={y(value)}
              y2={y(value)}
            />
            <text className="anomaly-axis-label" x={PAD.left - 8} y={y(value) + 4}>
              {niceValue(value)}
            </text>
          </g>
        ))}

        {bandRuns.map((band, index) => (
          <path
            key={`band-${index}`}
            className="anomaly-band"
            d={`M ${band.upper.split(" ").join(" L ")} L ${band.lower.split(" ").join(" L ")} Z`}
          />
        ))}

        {baselineRuns.map((run, index) => (
          <polyline key={`baseline-${index}`} className="anomaly-baseline" points={run} />
        ))}

        {runs.map((run, index) => (
          <polyline
            key={`series-${index}`}
            className="anomaly-series"
            points={run.map((p) => `${p.x},${p.y}`).join(" ")}
          />
        ))}

        {points.map((point) => {
          const { observation } = point;
          const unscored = observation.status !== "EVALUATED";
          const shape = observation.is_anomaly
            ? "anomaly-dot-flagged"
            : unscored
              ? "anomaly-dot-unscored"
              : "anomaly-dot";
          return (
            <g key={observation.period_start}>
              {observation.is_anomaly ? (
                <circle className="anomaly-dot-ring" cx={point.x} cy={point.y} r={7} />
              ) : null}
              <circle
                className={shape}
                cx={point.x}
                cy={point.y}
                r={observation.is_anomaly ? 4 : 3}
              >
                {/* Native hover, no client component required. */}
                <title>
                  {`${periodLabel(observation.period_start)}: ${observation.value}`}
                  {observation.status === "EVALUATED" && observation.baseline
                    ? ` (expected ${observation.baseline.expected_value.toFixed(1)})`
                    : ` (${observation.status.toLowerCase().replace(/_/g, " ")})`}
                </title>
              </circle>
            </g>
          );
        })}

        {/* Gaps are marked on the axis rather than in the line, so an absent
            period is visible without being drawn as a value. */}
        {series.map((observation, index) =>
          observation.value === null ? (
            <line
              key={`gap-${observation.period_start}`}
              className="anomaly-gap"
              x1={PAD.left + (series.length > 1 ? (index * PLOT_W) / (series.length - 1) : PLOT_W / 2)}
              x2={PAD.left + (series.length > 1 ? (index * PLOT_W) / (series.length - 1) : PLOT_W / 2)}
              y1={PAD.top}
              y2={PAD.top + PLOT_H}
            />
          ) : null,
        )}

        <text className="anomaly-axis-label" x={PAD.left} y={HEIGHT - 10}>
          {periodLabel(series[0].period_start)}
        </text>
        <text
          className="anomaly-axis-label anomaly-axis-end"
          x={WIDTH - PAD.right}
          y={HEIGHT - 10}
        >
          {periodLabel(series[series.length - 1].period_start)}
        </text>
      </svg>

      <ul className="anomaly-legend">
        <li>
          <span className="anomaly-key anomaly-key-series" aria-hidden="true" /> Actual
        </li>
        <li>
          <span className="anomaly-key anomaly-key-baseline" aria-hidden="true" /> Baseline
        </li>
        <li>
          <span className="anomaly-key anomaly-key-band" aria-hidden="true" /> Normal range
        </li>
        <li>
          <span className="anomaly-key anomaly-key-flagged" aria-hidden="true" /> Flagged
        </li>
        <li>
          <span className="anomaly-key anomaly-key-unscored" aria-hidden="true" /> Not judged
        </li>
      </ul>
    </div>
  );
}
