import type { InvestigationRequest, InvestigationResult } from "@/types/rca";

export const demoRequest: InvestigationRequest = {
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

export const fallbackResult: InvestigationResult = {
  anomaly: {
    metric_name: "Revenue",
    baseline_average: 10650,
    comparison_average: 8900,
    absolute_change: -1750,
    percent_change: -16.43,
    severity: "high",
  },
  top_drivers: [
    {
      dimension: "region",
      value: "North",
      baseline_value: 24200,
      comparison_value: 17200,
      absolute_change: -7000,
      contribution_pct: 46.67,
    },
    {
      dimension: "channel",
      value: "Paid",
      baseline_value: 24200,
      comparison_value: 17200,
      absolute_change: -7000,
      contribution_pct: 46.67,
    },
    {
      dimension: "region",
      value: "South",
      baseline_value: 18400,
      comparison_value: 18400,
      absolute_change: 0,
      contribution_pct: 0,
    },
  ],
  summary:
    "Revenue decreased by 16.43% versus baseline. Severity is high. The largest contributor is region=North.",
  recommended_actions: [
    "Review the North region paid acquisition campaign performance.",
    "Check tracking and revenue freshness for the comparison window.",
    "Annotate the investigation after the analyst confirms the likely cause.",
  ],
};
