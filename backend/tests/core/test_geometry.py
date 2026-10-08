import pytest

from app.core.clock import fmt_clock
from app.core.geometry import Point, distance_km, is_hostile_side, point_along, polyline_length_km
from app.scenario.generator import BORDER


def test_distance_and_polyline_length() -> None:
    a, b, c = Point(x_km=0, y_km=0), Point(x_km=3, y_km=4), Point(x_km=3, y_km=10)
    assert distance_km(a, b) == pytest.approx(5.0)
    assert polyline_length_km([a, b, c]) == pytest.approx(11.0)


def test_point_along_clamps_and_interpolates() -> None:
    line = [Point(x_km=0, y_km=0), Point(x_km=10, y_km=0)]
    assert point_along(line, 0.5) == Point(x_km=5, y_km=0)
    assert point_along(line, 2.0) == Point(x_km=10, y_km=0)


def test_border_sides() -> None:
    assert is_hostile_side(Point(x_km=800, y_km=350), BORDER)
    assert not is_hostile_side(Point(x_km=200, y_km=350), BORDER)


def test_point_rejects_coordinates_outside_theatre() -> None:
    with pytest.raises(ValueError):
        Point(x_km=-1, y_km=0)


@pytest.mark.parametrize(("minute", "label"), [(0, "05:00"), (95, "06:35"), (960, "21:00")])
def test_clock_labels(minute: int, label: str) -> None:
    assert fmt_clock(minute) == label
