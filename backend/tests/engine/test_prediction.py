import pytest

from app.core.constants import MAX_SERVICEABILITY, MIN_SERVICEABILITY
from app.domain.enums import AircraftStatus, TargetClass
from app.domain.models import Aircraft, Cop
from app.engine import prediction


def _aircraft(**kw: object) -> Aircraft:
    base = {"tail": "T-1", "type_code": "OMF", "base_id": "B-ALP", "hours_to_maintenance": 20.0}
    return Aircraft.model_validate(base | kw)


def test_serviceability_bounds() -> None:
    assert prediction.serviceability(_aircraft()) == pytest.approx(0.97)
    worn = _aircraft(snags_30d=20, sorties_today=5, hours_to_maintenance=1.0)
    assert prediction.serviceability(worn) == MIN_SERVICEABILITY
    assert prediction.serviceability(_aircraft(status=AircraftStatus.UNSERVICEABLE)) == 0.0
    assert prediction.serviceability(_aircraft()) <= MAX_SERVICEABILITY


def test_loss_risk_edges() -> None:
    assert prediction.loss_risk(0.0, 0.5) == 0.0
    assert prediction.loss_risk(100.0, 1.0) == 0.0
    assert 0 < prediction.loss_risk(50.0, 0.5) < prediction.loss_risk(100.0, 0.5) < 1


def test_weapon_effect_compounds_rounds(cop: Cop) -> None:
    lgb = cop.weapon("LGB")
    one, two = (prediction.weapon_effect(lgb, TargetClass.BRIDGE, n) for n in (1, 2))
    assert one == pytest.approx(0.8)
    assert two == pytest.approx(0.96)
    assert prediction.weapon_effect(cop.weapon("ARM"), TargetClass.BRIDGE, 2) == 0.0


def test_night_window() -> None:
    assert prediction.overlaps_night(0, 30)
    assert not prediction.overlaps_night(120, 300)
    assert prediction.overlaps_night(780, 900)
