from app.domain.enums import AircraftStatus
from app.domain.models import Cop
from app.domain.plan import Package, Sortie
from app.engine.validator import validate


def _sortie(cop: Cop, tail: str, crew: str, launch: int, qty: int = 2) -> Sortie:
    a = next(a for a in cop.aircraft if a.tail == tail)
    return Sortie(
        aircraft_tail=tail,
        type_code=a.type_code,
        crew_id=crew,
        base_id=a.base_id,
        weapon_code="LGB",
        weapon_qty=qty,
        launch_min=launch,
        tot_min=launch + 60,
        recover_min=launch + 120,
        route=[],
        route_km=300.0,
        exposure_km=0.0,
        risk=0.0,
        effect=0.9,
        p_success=0.9,
        needs_aar=False,
    )


def _package(mission_id: str, sorties: list[Sortie]) -> Package:
    return Package(
        mission_id=mission_id, tot_min=sorties[0].tot_min, sorties=sorties, expected_effect=0.9, risk=0.0
    )


def test_detects_double_booked_aircraft_and_crew(cop: Cop) -> None:
    a = _package("M-09", [_sortie(cop, "OMF-A01", "C-OMF-A01", 60), _sortie(cop, "OMF-A02", "C-OMF-A02", 60)])
    b = _package("M-10", [_sortie(cop, "OMF-A01", "C-OMF-A01", 90), _sortie(cop, "OMF-A03", "C-OMF-A03", 90)])
    violations = validate(cop, [a, b])
    assert any("OMF-A01: overlapping" in v for v in violations)
    assert any("crew C-OMF-A01: overlapping" in v for v in violations)


def test_detects_stock_overuse_and_unserviceable_aircraft(cop: Cop) -> None:
    for s in cop.stocks:
        if s.weapon_code == "LGB":
            s.quantity = 1
    down = next(a for a in cop.aircraft if a.status == AircraftStatus.UNSERVICEABLE)
    p = _package("M-09", [_sortie(cop, "OMF-A01", "C-OMF-A01", 60), _sortie(cop, down.tail, "C-OMF-A02", 60)])
    violations = validate(cop, [p])
    assert any("plan needs" in v for v in violations)
    assert any("unserviceable" in v for v in violations)


def test_detects_wrong_package_size_and_window(cop: Cop) -> None:
    p = _package("M-09", [_sortie(cop, "OMF-A01", "C-OMF-A01", 400)])
    violations = validate(cop, [p])
    assert any("needs 2" in v for v in violations)
    assert any("outside its window" in v for v in violations)
