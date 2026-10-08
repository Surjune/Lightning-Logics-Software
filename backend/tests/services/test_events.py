import pytest

from app.core.constants import SINGLE_SOURCE_CONFIDENCE
from app.core.exceptions import InvalidEventError
from app.core.geometry import Point
from app.domain.enums import MissionOrigin
from app.domain.events import (
    AircraftUnserviceableEvent,
    MunitionShortageEvent,
    SamPopupEvent,
    TimeSensitiveTargetEvent,
)
from app.services.container import Services


def test_correlated_sam_report_raises_confidence(services: Services) -> None:
    cop = services.picture.snapshot().cop
    known = cop.sams[1].model_copy(update={"confidence": 0.5})
    cop.sams[1].confidence = 0.5
    near = Point(x_km=known.position.x_km + 5, y_km=known.position.y_km)
    feed, proposal = services.events.apply(SamPopupEvent(position=near, range_km=40))
    sam = next(s for s in services.picture.snapshot().cop.sams if s.id == known.id)
    assert sam.confidence > known.confidence
    assert "correlated" in feed[0].summary
    assert proposal is None  # no approved plan yet, so nothing to retask


def test_new_hostile_sam_creates_sead_task(services: Services) -> None:
    services.events.apply(SamPopupEvent(position=Point(x_km=600, y_km=650), range_km=40))
    cop = services.picture.snapshot().cop
    new = cop.sams[-1]
    assert new.confidence == SINGLE_SOURCE_CONFIDENCE
    sead = cop.missions[-1]
    assert sead.origin == MissionOrigin.AUTO_SEAD
    assert sead.sam_id == new.id


def test_time_sensitive_target_becomes_mission(services: Services) -> None:
    services.events.apply(TimeSensitiveTargetEvent(position=Point(x_km=650, y_km=330), opens_in_min=30))
    tst = services.picture.snapshot().cop.missions[-1]
    assert tst.origin == MissionOrigin.TST
    assert tst.window_start_min == 30


def test_unknown_assets_are_rejected(services: Services) -> None:
    with pytest.raises(InvalidEventError):
        services.events.apply(AircraftUnserviceableEvent(tails=["NOPE-1"]))
    with pytest.raises(InvalidEventError):
        services.events.apply(MunitionShortageEvent(base_id="B-CHL", weapon_code="SOW", quantity=0))


def test_presets_resolve_against_current_plan(planned: Services) -> None:
    ids = {p.id for p in planned.events.presets()}
    assert {"ground", "sam", "tst", "base", "airspace"} <= ids
