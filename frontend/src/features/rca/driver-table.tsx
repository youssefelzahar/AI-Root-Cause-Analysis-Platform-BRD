import type { DriverFinding } from "@/types/rca";

export function DriverTable({ drivers }: { drivers: DriverFinding[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Dimension</th>
            <th>Value</th>
            <th>Baseline</th>
            <th>Comparison</th>
            <th>Change</th>
            <th>Contribution</th>
          </tr>
        </thead>
        <tbody>
          {drivers.map((driver) => (
            <tr key={`${driver.dimension}-${driver.value}`}>
              <td>{driver.dimension}</td>
              <td>{driver.value}</td>
              <td>{driver.baseline_value.toLocaleString()}</td>
              <td>{driver.comparison_value.toLocaleString()}</td>
              <td className={driver.absolute_change < 0 ? "negative-text" : "positive-text"}>
                {driver.absolute_change.toLocaleString()}
              </td>
              <td>{driver.contribution_pct}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
