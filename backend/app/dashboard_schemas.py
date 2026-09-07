from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .schemas import UTCResponse


class DashboardWindow(UTCResponse):
    days: int = Field(ge=1, le=90)
    created_from: datetime
    created_to: datetime


class DashboardCounts(BaseModel):
    total: int
    pending: int
    processing: int
    completed: int
    failed: int
    true_positive: int
    false_positive: int
    inconclusive: int
    unreviewed: int
    critical_high_allowed: int
    false_positive_denied: int


class DashboardSeverityCounts(BaseModel):
    CRITICAL: int
    HIGH: int
    MEDIUM: int
    LOW: int
    NONE: int
    UNKNOWN: int
    unrated: int


class DashboardProductionProfile(BaseModel):
    id: str
    name: str
    model_name: str


class DashboardRuntime(BaseModel):
    agent_mode: Literal["stub", "moduagent"]
    production_profile: DashboardProductionProfile | None
    source: Literal["api_configuration"] = "api_configuration"
    worker_health_verified: Literal[False] = False


class DashboardSummary(BaseModel):
    window: DashboardWindow
    counts: DashboardCounts
    severity_counts: DashboardSeverityCounts
    runtime: DashboardRuntime
