export type MetricPoint = {
  date: string;
  value: number;
  dimensions: Record<string, string>;
};

export type InvestigationRequest = {
  metric_name: string;
  baseline_period: MetricPoint[];
  comparison_period: MetricPoint[];
  dimensions: string[];
};

export type DriverFinding = {
  dimension: string;
  value: string;
  baseline_value: number;
  comparison_value: number;
  absolute_change: number;
  contribution_pct: number;
};

export type InvestigationResult = {
  anomaly: {
    metric_name: string;
    baseline_average: number;
    comparison_average: number;
    absolute_change: number;
    percent_change: number;
    severity: "low" | "medium" | "high" | "critical";
  };
  top_drivers: DriverFinding[];
  summary: string;
  recommended_actions: string[];
};
