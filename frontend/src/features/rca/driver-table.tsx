import type { DriverFinding } from "@/types/rca";

export function DriverTable({ drivers }: { drivers: DriverFinding[] }) {
  if (drivers.length === 0) {
    return <p className="muted">No dimensional drivers were provided.</p>;
  }

  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Dimension</th>
            <th>Value</th>
            <th className="numeric">Baseline</th>
            <th className="numeric">Comparison</th>
            <th className="numeric">Change</th>
            <th className="numeric">Contribution</th>
          </tr>
        </thead>
        <tbody>
          {drivers.map((driver) => (
            <tr key={`${driver.dimension}-${driver.value}`}>
              <td>{driver.dimension}</td>
              <td>
                <strong>{driver.value}</strong>
              </td>
              <td className="numeric">{driver.baseline_value.toLocaleString()}</td>
              <td className="numeric">{driver.comparison_value.toLocaleString()}</td>
              <td className={`numeric ${driver.absolute_change < 0 ? "negative-text" : "positive-text"}`}>
                {driver.absolute_change.toLocaleString()}
              </td>
              <td className="numeric">{driver.contribution_pct}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
