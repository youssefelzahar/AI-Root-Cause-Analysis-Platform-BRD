import { cn, formatStat } from "@/lib/format";
import type { Driver, KpiChange } from "@/types/rca";

import { segmentLabel } from "./driver-table";

type Step = {
  key: string;
  label: string;
  from: number;
  to: number;
  kind: "total" | "up" | "down";
  value: number;
};

/**
 * Previous total, each named driver's step, the unexplained remainder, then the
 * current total. Floating bars positioned from a running cumulative - plain
 * arithmetic, no charting library.
 */
function buildSteps(kpi: KpiChange, drivers: Driver[]): Step[] {
  const previous = kpi.previous_value ?? 0;
  const current = kpi.current_value ?? 0;

  const steps: Step[] = [
    { key: "previous", label: "Previous", from: 0, to: previous, kind: "total", value: previous },
  ];

  let running = previous;
  let named = 0;
  for (const driver of drivers) {
    const delta = driver.absolute_change ?? 0;
    if (delta === 0) continue;
    steps.push({
      key: driver.node_id,
      label: segmentLabel(driver),
      from: running,
      to: running + delta,
      kind: delta < 0 ? "down" : "up",
      value: delta,
    });
    running += delta;
    named += delta;
  }

  const remainder = (current - previous) - named;
  if (Math.abs(remainder) > Math.abs(current - previous) * 0.005) {
    steps.push({
      key: "other",
      label: "All other segments",
      from: running,
      to: running + remainder,
      kind: remainder < 0 ? "down" : "up",
      value: remainder,
    });
  }

  steps.push({
    key: "current",
    label: "Current",
    from: 0,
    to: current,
    kind: "total",
    value: current,
  });
  return steps;
}

export function Waterfall({ kpi, drivers }: { kpi: KpiChange; drivers: Driver[] }) {
  const steps = buildSteps(kpi, drivers);
  const points = steps.flatMap((step) => [step.from, step.to]).concat(0);
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;

  return (
    <div className="rca-waterfall">
      {steps.map((step) => {
        const left = ((Math.min(step.from, step.to) - min) / span) * 100;
        const width = Math.max((Math.abs(step.to - step.from) / span) * 100, 0.5);
        return (
          <div className="rca-waterfall-row" key={step.key}>
            <span className="rca-waterfall-label">{step.label}</span>
            <span className="rca-waterfall-track" aria-hidden="true">
              <span
                className={cn("rca-waterfall-fill", step.kind)}
                style={{ left: `${left}%`, width: `${width}%` }}
              />
            </span>
            <span
              className={cn(
                "rca-waterfall-value",
                step.kind === "down" && "negative-text",
                step.kind === "up" && "positive-text",
              )}
            >
              {formatStat(step.value)}
            </span>
          </div>
        );
      })}
    </div>
  );
}
