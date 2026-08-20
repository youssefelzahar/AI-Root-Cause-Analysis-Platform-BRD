import { Pill } from "@/components/ui";
import { formatPercent, formatStat } from "@/lib/format";
import type { Driver } from "@/types/rca";

import { ContributionBar } from "./contribution-bar";

export function segmentLabel(driver: Driver): string {
  if (driver.value_is_null) return "(no value)";
  return driver.value ?? "—";
}

/**
 * One driver list. Contributions may exceed 100% or be negative - both are
 * correct rather than errors, and the sign is what distinguishes a driver from
 * an offsetting factor, so it is never hidden.
 */
export function DriverTable({
  drivers,
  emptyMessage,
  showEffects = false,
}: {
  drivers: Driver[];
  emptyMessage: string;
  showEffects?: boolean;
}) {
  if (drivers.length === 0) {
    return <p className="muted">{emptyMessage}</p>;
  }

  const scale = Math.max(...drivers.map((d) => Math.abs(d.contribution ?? 0)), 0.0001);
  const attributable = drivers.some((d) => d.contribution !== null);

  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Segment</th>
            <th className="numeric">Previous</th>
            <th className="numeric">Current</th>
            <th className="numeric">Change</th>
            {attributable ? <th className="numeric">Contribution</th> : null}
            {attributable ? <th /> : null}
            {showEffects ? <th className="numeric">Rate</th> : null}
            {showEffects ? <th className="numeric">Mix</th> : null}
          </tr>
        </thead>
        <tbody>
          {drivers.map((driver) => {
            const change = driver.absolute_change ?? 0;
            return (
              <tr key={driver.node_id}>
                <td>
                  <span className="muted">{driver.dimension}</span> {segmentLabel(driver)}
                  {driver.is_new_segment ? (
                    <>
                      {" "}
                      <Pill tone="info">New</Pill>
                    </>
                  ) : null}
                  {driver.is_lost_segment ? (
                    <>
                      {" "}
                      <Pill tone="warning">Gone</Pill>
                    </>
                  ) : null}
                  {driver.low_support ? (
                    <>
                      {" "}
                      <Pill tone="neutral">Few rows</Pill>
                    </>
                  ) : null}
                </td>
                <td className="numeric">{formatStat(driver.previous_value)}</td>
                <td className="numeric">{formatStat(driver.current_value)}</td>
                <td className={`numeric ${change < 0 ? "negative-text" : "positive-text"}`}>
                  {formatStat(driver.absolute_change)}
                </td>
                {attributable ? (
                  <td className="numeric">
                    {driver.contribution === null
                      ? "—"
                      : formatPercent(driver.contribution * 100, 0)}
                  </td>
                ) : null}
                {attributable ? (
                  <td>
                    <ContributionBar value={driver.contribution} scale={scale} />
                  </td>
                ) : null}
                {showEffects ? (
                  <td className="numeric">{formatStat(driver.rate_effect)}</td>
                ) : null}
                {showEffects ? <td className="numeric">{formatStat(driver.mix_effect)}</td> : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
