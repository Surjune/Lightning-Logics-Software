"""Read models served to the dashboard: the whole picture in one document."""

from typing import Literal

from pydantic import BaseModel

from app.domain.enums import SourceSystem
from app.domain.models import Cop
from app.domain.plan import Plan, PlanKpis, Proposal, UnassignedMission

Health = Literal["GREEN", "AMBER", "RED"]


class SourceHealth(BaseModel):
    source: SourceSystem
    system_name: str
    age_min: int
    health: Health
    records: int


class OperationalPicture(BaseModel):
    clock_min: int
    clock_label: str
    # Clock reference so the dashboard never duplicates backend constants.
    day_start_clock_min: int
    horizon_min: int
    arming_lead_time_min: int
    cop: Cop
    plan: Plan
    plan_kpis: PlanKpis
    plan_violations: list[str]
    plan_unassigned: list[UnassignedMission]
    proposal: Proposal | None
    sources: list[SourceHealth]
