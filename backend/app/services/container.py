"""Wires the services around one shared operational state."""

from dataclasses import dataclass

from app.core.constants import SCENARIO_SEED
from app.services.audit import AuditLog
from app.services.clock import ClockService
from app.services.events import EventService
from app.services.picture import PictureService
from app.services.planning import PlanningService
from app.services.state import OpsState


@dataclass(frozen=True)
class Services:
    picture: PictureService
    planning: PlanningService
    events: EventService
    clock: ClockService
    audit: AuditLog


def build_services(audit: AuditLog, seed: int = SCENARIO_SEED) -> Services:
    state = OpsState.fresh(seed, audit)
    planning = PlanningService(state)
    return Services(
        picture=PictureService(state, planning),
        planning=planning,
        events=EventService(state, planning),
        clock=ClockService(state),
        audit=audit,
    )
