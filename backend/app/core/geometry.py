"""Planar geometry for the theatre. Coordinates are kilometres, x east and y north."""

import math
from collections.abc import Sequence
from itertools import pairwise

from pydantic import BaseModel, ConfigDict, Field

from app.core.constants import THEATRE_HEIGHT_KM, THEATRE_WIDTH_KM


class Point(BaseModel):
    """A theatre position in km. Lives in core so every layer can share it."""

    model_config = ConfigDict(frozen=True)

    x_km: float = Field(ge=0.0, le=THEATRE_WIDTH_KM)
    y_km: float = Field(ge=0.0, le=THEATRE_HEIGHT_KM)


def distance_km(a: Point, b: Point) -> float:
    return math.hypot(a.x_km - b.x_km, a.y_km - b.y_km)


def in_circle(p: Point, centre: Point, radius_km: float) -> bool:
    return distance_km(p, centre) <= radius_km


def polyline_length_km(points: Sequence[Point]) -> float:
    return sum(distance_km(a, b) for a, b in pairwise(points))


def point_along(points: Sequence[Point], fraction: float) -> Point:
    """Point at `fraction` (0-1) of the way along a polyline."""
    total = polyline_length_km(points)
    target = total * min(max(fraction, 0.0), 1.0)
    walked = 0.0
    for a, b in pairwise(points):
        seg = distance_km(a, b)
        if walked + seg >= target and seg > 0:
            t = (target - walked) / seg
            return Point(x_km=a.x_km + t * (b.x_km - a.x_km), y_km=a.y_km + t * (b.y_km - a.y_km))
        walked += seg
    return points[-1]


def x_on_border(border: Sequence[Point], y_km: float) -> float:
    """Border x at a given y, by linear interpolation along the border polyline."""
    for a, b in pairwise(border):
        lo, hi = sorted((a.y_km, b.y_km))
        if lo <= y_km <= hi and hi > lo:
            t = (y_km - a.y_km) / (b.y_km - a.y_km)
            return a.x_km + t * (b.x_km - a.x_km)
    return border[0].x_km


def is_hostile_side(p: Point, border: Sequence[Point]) -> bool:
    """Redland lies east of the border."""
    return p.x_km > x_on_border(border, p.y_km)
