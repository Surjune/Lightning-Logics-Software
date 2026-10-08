"""Threat-aware routing over a theatre grid.

One Dijkstra sweep per origin base covers every target at once. Each step costs its
length scaled up by the SAM threat in the cell entered, so routes bend around SAM rings
when a detour is cheaper than the exposure. Restricted airspace and storm cells are
impassable. The resulting grid path is then smoothed wherever a straight leg stays out
of every threat ring.
"""

import heapq
import math
from dataclasses import dataclass, field
from itertools import pairwise

from app.core.constants import (
    ROUTE_SAMPLE_KM,
    ROUTING_CELL_KM,
    THEATRE_HEIGHT_KM,
    THEATRE_WIDTH_KM,
    THREAT_AVOIDANCE_FACTOR,
)
from app.core.geometry import Point, distance_km, in_circle
from app.domain.enums import WeatherCondition
from app.domain.models import Cop

COLS = int(THEATRE_WIDTH_KM / ROUTING_CELL_KM)
ROWS = int(THEATRE_HEIGHT_KM / ROUTING_CELL_KM)
NEIGHBOURS = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]


@dataclass(frozen=True)
class ThreatRing:
    centre: Point
    radius_km: float
    weight: float  # lethality x confidence


@dataclass(frozen=True)
class ReleaseLeg:
    """The route from a base to a weapon-release point `standoff_km` short of the target."""

    points: tuple[Point, ...]
    km: float
    exposure_km: float  # one-way, lethality-weighted


@dataclass(frozen=True)
class RouteProfile:
    reachable: bool
    vertices: tuple[Point, ...] = ()
    vertex_cum_km: tuple[float, ...] = ()
    sample_cum_km: tuple[float, ...] = ()
    sample_dist_to_target: tuple[float, ...] = ()
    sample_cum_exposure: tuple[float, ...] = ()

    def to_release(self, standoff_km: float) -> ReleaseLeg:
        idx = len(self.sample_cum_km) - 1
        for i, d in enumerate(self.sample_dist_to_target):
            if d <= standoff_km:
                idx = i
                break
        release_km = self.sample_cum_km[idx]
        points = [v for v, c in zip(self.vertices, self.vertex_cum_km, strict=True) if c < release_km]
        points.append(_point_at(self.vertices, self.vertex_cum_km, release_km))
        return ReleaseLeg(points=tuple(points), km=release_km, exposure_km=self.sample_cum_exposure[idx])


def _point_at(vertices: tuple[Point, ...], cum: tuple[float, ...], km: float) -> Point:
    for i in range(1, len(vertices)):
        if cum[i] >= km:
            seg = cum[i] - cum[i - 1]
            t = 0.0 if seg == 0 else (km - cum[i - 1]) / seg
            a, b = vertices[i - 1], vertices[i]
            return Point(x_km=a.x_km + t * (b.x_km - a.x_km), y_km=a.y_km + t * (b.y_km - a.y_km))
    return vertices[-1]


def _cell_of(p: Point) -> tuple[int, int]:
    i = min(max(int(p.x_km / ROUTING_CELL_KM), 0), COLS - 1)
    j = min(max(int(p.y_km / ROUTING_CELL_KM), 0), ROWS - 1)
    return i, j


def _centre(i: int, j: int) -> Point:
    return Point(x_km=(i + 0.5) * ROUTING_CELL_KM, y_km=(j + 0.5) * ROUTING_CELL_KM)


@dataclass
class Router:
    rings: list[ThreatRing]
    blocked: list[bool]
    cell_threat: list[float]
    _sweeps: dict[tuple[int, int], tuple[list[float], list[int]]] = field(default_factory=dict)
    _profiles: dict[tuple[float, float, float, float], RouteProfile] = field(default_factory=dict)

    @classmethod
    def from_cop(cls, cop: Cop) -> "Router":
        rings = [ThreatRing(s.position, s.range_km, s.lethality * s.confidence) for s in cop.sams if s.active]
        obstacles = [(z.centre, z.radius_km) for z in cop.airspace if z.active]
        obstacles += [(w.centre, w.radius_km) for w in cop.weather if w.condition == WeatherCondition.STORM]
        blocked: list[bool] = []
        cell_threat: list[float] = []
        for j in range(ROWS):
            for i in range(COLS):
                c = _centre(i, j)
                blocked.append(any(in_circle(c, centre, r) for centre, r in obstacles))
                cell_threat.append(sum(r.weight for r in rings if in_circle(c, r.centre, r.radius_km)))
        return cls(rings=rings, blocked=blocked, cell_threat=cell_threat)

    def threat_at(self, p: Point) -> float:
        return sum(r.weight for r in self.rings if in_circle(p, r.centre, r.radius_km))

    def _sweep(self, origin: tuple[int, int]) -> tuple[list[float], list[int]]:
        if origin in self._sweeps:
            return self._sweeps[origin]
        dist = [math.inf] * (COLS * ROWS)
        pred = [-1] * (COLS * ROWS)
        start = origin[1] * COLS + origin[0]
        dist[start] = 0.0
        heap = [(0.0, start)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist[u]:
                continue
            ui, uj = u % COLS, u // COLS
            for di, dj in NEIGHBOURS:
                vi, vj = ui + di, uj + dj
                if not (0 <= vi < COLS and 0 <= vj < ROWS):
                    continue
                v = vj * COLS + vi
                if self.blocked[v]:
                    continue
                step = ROUTING_CELL_KM * (math.sqrt(2) if di and dj else 1.0)
                nd = d + step * (1.0 + THREAT_AVOIDANCE_FACTOR * self.cell_threat[v])
                if nd < dist[v]:
                    dist[v] = nd
                    pred[v] = u
                    heapq.heappush(heap, (nd, v))
        self._sweeps[origin] = (dist, pred)
        return dist, pred

    def route(self, origin: Point, target: Point) -> RouteProfile:
        key = (origin.x_km, origin.y_km, target.x_km, target.y_km)
        if key in self._profiles:
            return self._profiles[key]
        oi, oj = _cell_of(origin)
        ti, tj = _cell_of(target)
        dist, pred = self._sweep((oi, oj))
        goal = tj * COLS + ti
        if math.isinf(dist[goal]):
            profile = RouteProfile(reachable=False)
        else:
            cells: list[int] = []
            u = goal
            while u != -1 and u != oj * COLS + oi:
                cells.append(u)
                u = pred[u]
            cells.reverse()
            path = [origin] + [_centre(c % COLS, c // COLS) for c in cells[:-1]] + [target]
            profile = self._profile(self._smooth(path), target)
        self._profiles[key] = profile
        return profile

    def _leg_is_clear(self, a: Point, b: Point, allowed_threat: float) -> bool:
        steps = max(int(distance_km(a, b) / (ROUTING_CELL_KM / 2)), 1)
        for s in range(steps + 1):
            t = s / steps
            p = Point(x_km=a.x_km + t * (b.x_km - a.x_km), y_km=a.y_km + t * (b.y_km - a.y_km))
            i, j = _cell_of(p)
            if self.blocked[j * COLS + i] or self.threat_at(p) > allowed_threat:
                return False
        return True

    def _smooth(self, path: list[Point]) -> list[Point]:
        """Shortcut grid steps where a straight leg adds no threat over the grid path."""
        threats = [self.threat_at(p) for p in path]
        out = [path[0]]
        i = 0
        while i < len(path) - 1:
            # Binary search for the farthest vertex reachable by a clear straight leg.
            lo, hi = i + 1, len(path) - 1
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if self._leg_is_clear(path[i], path[mid], min(threats[i : mid + 1])):
                    lo = mid
                else:
                    hi = mid - 1
            out.append(path[lo])
            i = lo
        return out

    def _profile(self, vertices: list[Point], target: Point) -> RouteProfile:
        vertex_cum = [0.0]
        for a, b in pairwise(vertices):
            vertex_cum.append(vertex_cum[-1] + distance_km(a, b))
        sample_cum: list[float] = []
        sample_dist: list[float] = []
        sample_exp: list[float] = []
        exposure = 0.0
        prev: Point | None = None
        for a, b, start_km in zip(vertices, vertices[1:], vertex_cum, strict=False):
            seg = distance_km(a, b)
            n = max(int(seg / ROUTE_SAMPLE_KM), 1)
            for s in range(n + 1):
                if prev is not None and s == 0:
                    continue
                t = s / n
                p = Point(x_km=a.x_km + t * (b.x_km - a.x_km), y_km=a.y_km + t * (b.y_km - a.y_km))
                if prev is not None:
                    exposure += distance_km(prev, p) * self.threat_at(p)
                sample_cum.append(start_km + t * seg)
                sample_dist.append(distance_km(p, target))
                sample_exp.append(exposure)
                prev = p
        return RouteProfile(
            reachable=True,
            vertices=tuple(vertices),
            vertex_cum_km=tuple(vertex_cum),
            sample_cum_km=tuple(sample_cum),
            sample_dist_to_target=tuple(sample_dist),
            sample_cum_exposure=tuple(sample_exp),
        )


def router_signature(cop: Cop) -> tuple[object, ...]:
    """Everything that changes routes. Routers are reused while this is unchanged."""
    return (
        tuple(
            (s.id, s.position.x_km, s.position.y_km, s.range_km, s.lethality, s.confidence, s.active)
            for s in cop.sams
        ),
        tuple((z.id, z.active) for z in cop.airspace),
        tuple((w.id, w.centre.x_km, w.centre.y_km, w.radius_km, w.condition) for w in cop.weather),
    )


class RouterCache:
    def __init__(self) -> None:
        self._signature: tuple[object, ...] | None = None
        self._router: Router | None = None

    def get(self, cop: Cop) -> Router:
        sig = router_signature(cop)
        if self._router is None or sig != self._signature:
            self._router = Router.from_cop(cop)
            self._signature = sig
        return self._router
