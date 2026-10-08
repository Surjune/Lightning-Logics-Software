"""HTTP routes. Each parses its input, calls one service, and returns the result.

Every state-changing route returns the full operational picture, so the dashboard
replaces its state in one step and never shows a half-updated view.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from app.api.schemas import AdvanceClockRequest, ResetRequest
from app.domain.events import EventPreset, SituationEvent
from app.domain.picture import OperationalPicture
from app.domain.plan import MissionExplanation
from app.services.audit import AuditEntry
from app.services.container import Services

router = APIRouter()

CoaId = Annotated[str, Path(pattern=r"^[A-Z]$")]
MissionId = Annotated[str, Path(pattern=r"^M-\d{2,3}$")]


def services(request: Request) -> Services:
    svc: Services = request.app.state.services
    return svc


Svc = Annotated[Services, Depends(services)]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/state", response_model=OperationalPicture)
def get_state(svc: Svc) -> OperationalPicture:
    return svc.picture.snapshot()


@router.post("/plan/generate", response_model=OperationalPicture)
def generate_plan(svc: Svc) -> OperationalPicture:
    svc.planning.generate("Planning request: full air tasking order")
    return svc.picture.snapshot()


@router.post("/proposal/{coa_id}/approve", response_model=OperationalPicture)
def approve(coa_id: CoaId, svc: Svc) -> OperationalPicture:
    svc.planning.approve(coa_id)
    return svc.picture.snapshot()


@router.post("/proposal/reject", response_model=OperationalPicture)
def reject(svc: Svc) -> OperationalPicture:
    svc.planning.reject()
    return svc.picture.snapshot()


@router.post("/events", response_model=OperationalPicture)
def inject_event(event: SituationEvent, svc: Svc) -> OperationalPicture:
    svc.events.apply(event)
    return svc.picture.snapshot()


@router.get("/events/presets", response_model=list[EventPreset])
def event_presets(svc: Svc) -> list[EventPreset]:
    return svc.events.presets()


@router.post("/clock/advance", response_model=OperationalPicture)
def advance_clock(body: AdvanceClockRequest, svc: Svc) -> OperationalPicture:
    svc.clock.advance(body.minutes)
    return svc.picture.snapshot()


@router.post("/scenario/reset", response_model=OperationalPicture)
def reset(body: ResetRequest, svc: Svc) -> OperationalPicture:
    return svc.picture.reset(body.seed)


@router.get("/missions/{mission_id}/explain", response_model=MissionExplanation)
def explain(mission_id: MissionId, svc: Svc) -> MissionExplanation:
    return svc.planning.explain(mission_id)


@router.get("/audit", response_model=list[AuditEntry])
def audit(svc: Svc) -> list[AuditEntry]:
    return svc.audit.recent()
