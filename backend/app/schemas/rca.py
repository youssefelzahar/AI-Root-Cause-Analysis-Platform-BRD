from pydantic import BaseModel, Field


class MetricPoint(BaseModel):
    date: str
    value: float
    dimensions: dict[str, str] = Field(default_factory=dict)


class InvestigationRequest(BaseModel):
    metric_name: str
    baseline_period: list[MetricPoint]
    comparison_period: list[MetricPoint]
    dimensions: list[str] = Field(default_factory=list)


class DriverFinding(BaseModel):
    dimension: str
    value: str
    baseline_value: float
    comparison_value: float
    absolute_change: float
    contribution_pct: float


class AnomalyResult(BaseModel):
    metric_name: str
    baseline_average: float
    comparison_average: float
    absolute_change: float
    percent_change: float
    severity: str


class InvestigationResult(BaseModel):
    anomaly: AnomalyResult
    top_drivers: list[DriverFinding]
    summary: str
    recommended_actions: list[str]
