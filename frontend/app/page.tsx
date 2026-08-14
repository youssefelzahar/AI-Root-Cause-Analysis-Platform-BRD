import { Activity, AlertTriangle, Brain, Database, LineChart, PlayCircle } from "lucide-react";

import { DriverTable } from "@/components/DriverTable";
import { StatTile } from "@/components/StatTile";
import { runDemoInvestigation } from "@/lib/api";

export default async function Home() {
  const result = await runDemoInvestigation();
  const anomaly = result.anomaly;
  const directionTone = anomaly.absolute_change < 0 ? "negative" : "positive";

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <Brain size={26} />
          <div>
            <strong>AI RCA</strong>
            <span>Investigation Console</span>
          </div>
        </div>
        <nav>
          <a className="active"><Activity size={17} /> Alerts</a>
          <a><LineChart size={17} /> Investigations</a>
          <a><Database size={17} /> Datasets</a>
        </nav>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Active incident</p>
            <h1>{anomaly.metric_name} anomaly investigation</h1>
          </div>
          <button type="button">
            <PlayCircle size={18} />
            Run RCA
          </button>
        </header>

        <section className="alert-band">
          <AlertTriangle size={24} />
          <div>
            <span>{anomaly.severity.toUpperCase()} SEVERITY</span>
            <p>{result.summary}</p>
          </div>
        </section>

        <section className="stats-grid">
          <StatTile label="Baseline avg" value={anomaly.baseline_average.toLocaleString()} />
          <StatTile label="Comparison avg" value={anomaly.comparison_average.toLocaleString()} />
          <StatTile label="Absolute change" value={anomaly.absolute_change.toLocaleString()} tone={directionTone} />
          <StatTile label="Percent change" value={`${anomaly.percent_change}%`} tone={directionTone} />
        </section>

        <section className="content-grid">
          <div className="panel wide">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Evidence</p>
                <h2>Top contributing drivers</h2>
              </div>
            </div>
            <DriverTable drivers={result.top_drivers} />
          </div>

          <div className="panel">
            <p className="eyebrow">Recommended actions</p>
            <h2>Next steps</h2>
            <ul className="actions">
              {result.recommended_actions.map((action) => (
                <li key={action}>{action}</li>
              ))}
            </ul>
          </div>
        </section>
      </section>
    </main>
  );
}
