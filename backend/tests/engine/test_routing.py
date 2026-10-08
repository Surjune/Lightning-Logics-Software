from app.core.geometry import Point, distance_km, in_circle
from app.domain.enums import WeatherCondition
from app.domain.models import Cop, WeatherCell
from app.engine.routing import Router


def test_clear_route_is_direct_and_unexposed(cop: Cop) -> None:
    router = Router.from_cop(cop)
    a, b = cop.base("B-DLT").position, cop.base("B-BRV").position
    profile = router.route(a, b)
    leg = profile.to_release(0.0)
    assert profile.reachable
    assert leg.exposure_km == 0.0
    assert leg.km <= distance_km(a, b) * 1.05


def test_route_detours_around_storm(cop: Cop) -> None:
    storm_centre = Point(x_km=250, y_km=300)
    cop.weather.append(
        WeatherCell(id="W-T", centre=storm_centre, radius_km=40, condition=WeatherCondition.STORM)
    )
    router = Router.from_cop(cop)
    leg = router.route(cop.base("B-DLT").position, Point(x_km=400, y_km=330)).to_release(0.0)
    assert not any(in_circle(p, storm_centre, 30) for p in leg.points)


def test_standoff_release_shortens_leg_and_exposure(cop: Cop) -> None:
    router = Router.from_cop(cop)
    profile = router.route(cop.base("B-ALP").position, cop.mission("M-26").position)
    direct, standoff = profile.to_release(0.0), profile.to_release(150.0)
    assert standoff.km < direct.km
    assert standoff.exposure_km < direct.exposure_km
