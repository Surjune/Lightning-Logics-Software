from app.domain.enums import AircraftStatus, RejectReason
from app.domain.models import Cop
from app.engine.candidates import Commitments, generate_candidates
from app.engine.routing import Router


def test_unserviceable_aircraft_never_offered(cop: Cop) -> None:
    cset = generate_candidates(cop, Router.from_cop(cop), cop.missions, 0, Commitments())
    down = {a.tail for a in cop.aircraft if a.status == AircraftStatus.UNSERVICEABLE}
    assert down
    assert not any(c.tail in down for c in cset.candidates)
    assert any(RejectReason.AIRCRAFT_UNSERVICEABLE in r for r in cset.rejections.values())


def test_cloud_over_target_excludes_laser_and_visual_weapons(cop: Cop) -> None:
    mission = cop.mission("M-15")  # under cloud cell W-01
    cset = generate_candidates(cop, Router.from_cop(cop), [mission], 0, Commitments())
    weapons = {c.weapon_code for c in cset.by_mission["M-15"]}
    assert weapons
    assert not weapons & {"LGB", "UGB"}


def test_reserved_stock_is_not_offered(cop: Cop) -> None:
    mission = cop.mission("M-09")
    commitments = Commitments()
    for s in cop.stocks:
        commitments.stock_reserved[(s.base_id, s.weapon_code)] = s.quantity
    cset = generate_candidates(cop, Router.from_cop(cop), [mission], 0, commitments)
    assert not cset.by_mission.get("M-09")
    assert RejectReason.NO_WEAPON_STOCK in cset.rejections["M-09"]


def test_candidates_respect_mission_window_and_lead_time(cop: Cop) -> None:
    now = 100
    cset = generate_candidates(cop, Router.from_cop(cop), cop.missions, now, Commitments())
    for c in cset.candidates:
        m = cop.mission(c.mission_id)
        assert m.window_start_min <= c.earliest_tot <= c.latest_tot <= m.window_end_min
        assert c.earliest_tot - c.pre_min >= now
