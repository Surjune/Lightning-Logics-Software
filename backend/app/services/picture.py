"""Read-side assembly of the full operational picture for the dashboard."""

from app.core.clock import fmt_clock
from app.core.constants import (
    ARMING_LEAD_TIME_MIN,
    DAY_START_CLOCK_MIN,
    HORIZON_MIN,
    SCENARIO_SEED,
    SOURCE_STALE_AMBER_MIN,
    SOURCE_STALE_RED_MIN,
)
from app.domain.models import Cop
from app.domain.picture import Health, OperationalPicture, SourceHealth
from app.services.planning import PlanningService
from app.services.state import OpsState


def _health(age: int) -> Health:
    if age >= SOURCE_STALE_RED_MIN:
        return "RED"
    if age >= SOURCE_STALE_AMBER_MIN:
        return "AMBER"
    return "GREEN"


def source_health(cop: Cop) -> list[SourceHealth]:
    return [
        SourceHealth(
            source=s.source,
            system_name=s.system_name,
            age_min=cop.clock_min - s.last_update_min,
            health=_health(cop.clock_min - s.last_update_min),
            records=s.records,
        )
        for s in cop.sources
    ]


class PictureService:
    def __init__(self, state: OpsState, planning: PlanningService) -> None:
        self.state = state
        self.planning = planning

    def snapshot(self) -> OperationalPicture:
        with self.state.lock:
            kpis, violations = self.planning.current_kpis()
            cop = self.state.cop
            return OperationalPicture(
                clock_min=cop.clock_min,
                clock_label=fmt_clock(cop.clock_min),
                day_start_clock_min=DAY_START_CLOCK_MIN,
                horizon_min=HORIZON_MIN,
                arming_lead_time_min=ARMING_LEAD_TIME_MIN,
                cop=cop,
                plan=self.state.plan,
                plan_kpis=kpis,
                plan_violations=violations,
                plan_unassigned=self.planning.current_unassigned() if self.state.plan.packages else [],
                proposal=self.state.proposal,
                sources=source_health(cop),
            )

    def reset(self, seed: int | None) -> OperationalPicture:
        with self.state.lock:
            chosen = SCENARIO_SEED if seed is None else seed
            self.state.reset(chosen)
            self.state.audit.record(0, "commander", "RESET", f"Scenario reset with seed {chosen}")
            return self.snapshot()
