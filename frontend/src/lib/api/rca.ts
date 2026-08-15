import type { InvestigationResult } from "@/types/rca";

import { apiFetch } from "./http";

const DEMO_REQUEST = {
  metric_name: "Revenue",
  baseline_period: [
    { date: "2026-08-01", value: 12000, dimensions: { region: "North", channel: "Paid" } },
    { date: "2026-08-01", value: 9000, dimensions: { region: "South", channel: "Organic" } },
    { date: "2026-08-02", value: 12200, dimensions: { region: "North", channel: "Paid" } },
    { date: "2026-08-02", value: 9400, dimensions: { region: "South", channel: "Organic" } },
  ],
  comparison_period: [
    { date: "2026-08-08", value: 8700, dimensions: { region: "North", channel: "Paid" } },
    { date: "2026-08-08", value: 9300, dimensions: { region: "South", channel: "Organic" } },
    { date: "2026-08-09", value: 8500, dimensions: { region: "North", channel: "Paid" } },
    { date: "2026-08-09", value: 9100, dimensions: { region: "South", channel: "Organic" } },
  ],
  dimensions: ["region", "channel"],
};

/**
 * No try/catch and no fallback result: a backend outage must be visible.
 */
export function runDemoInvestigation(): Promise<InvestigationResult> {
  return apiFetch<InvestigationResult>("/api/investigations", {
    method: "POST",
    body: DEMO_REQUEST,
  });
}
