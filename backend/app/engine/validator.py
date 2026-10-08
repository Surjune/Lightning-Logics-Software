"""Independent hard-constraint check of a plan.

Written separately from the optimiser and reading only the picture and the plan, so a
modelling bug in the solver shows up here as a violation instead of reaching a crew.
"""

from collections import defaultdict
from itertools import pairwise

from app.core.constants import (
    AAR_RADIUS_FACTOR,
    CREW_MAX_DUTY_MIN,
    CREW_MAX_SORTIES_PER_DAY,
    CREW_MIN_REST_MIN,
    TANKER_RECEIVERS_PER_SORTIE,
    TANKER_SORTIES_PER_DAY,
)
from app.domain.enums import AircraftStatus, PackageStatus, WeatherCondition
from app.domain.models import Cop
from app.domain.plan import Package
from app.engine import prediction
from app.scenario.catalogue import MISSION_WEAPON_KINDS

PENDING = (PackageStatus.PLANNED, PackageStatus.COMMITTED)
# Rounding slack when comparing route length (km) to combat radius.
RANGE_TOLERANCE_KM = 0.5


def _overlaps(intervals: list[tuple[int, int, str]]) -> list[tuple[str, str]]:
    clashes = []
    ordered = sorted(intervals)
    for (_, end_a, a), (start_b, _, b) in pairwise(ordered):
        if start_b < end_a:
            clashes.append((a, b))
    return clashes


def validate(cop: Cop, packages: list[Package]) -> list[str]:
    v: list[str] = []
    aircraft = {a.tail: a for a in cop.aircraft}
    crews = {c.id: c for c in cop.crews}
    missions = {m.id: m for m in cop.missions}

    aircraft_use: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    crew_use: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    stock_use: dict[tuple[str, str], int] = defaultdict(int)
    aircraft_minutes: dict[str, int] = defaultdict(int)
    crew_minutes: dict[str, int] = defaultdict(int)
    crew_sorties: dict[str, int] = defaultdict(int)
    aar_receivers = 0

    for p in packages:
        m = missions.get(p.mission_id)
        if m is None:
            v.append(f"{p.mission_id}: mission not in the picture")
            continue
        if len(p.sorties) != m.aircraft_required:
            v.append(f"{m.id}: package has {len(p.sorties)} aircraft, needs {m.aircraft_required}")
        if not m.window_start_min <= p.tot_min <= m.window_end_min:
            v.append(f"{m.id}: time on target outside its window")
        night = prediction.overlaps_night(m.window_start_min, m.window_end_min)
        target_wx = prediction.weather_at(m.position, cop.weather)
        for s in p.sorties:
            a = aircraft.get(s.aircraft_tail)
            crew = crews.get(s.crew_id)
            if a is None or crew is None:
                v.append(f"{m.id}: unknown aircraft {s.aircraft_tail} or crew {s.crew_id}")
                continue
            atype = cop.aircraft_type(a.type_code)
            label = f"{m.id}/{a.tail}"
            turnaround = atype.turnaround_min
            aircraft_use[a.tail].append((s.launch_min, s.recover_min + turnaround, m.id))
            crew_use[crew.id].append((s.launch_min, s.recover_min + CREW_MIN_REST_MIN, m.id))
            aar_receivers += int(s.needs_aar)
            if p.status not in PENDING:
                continue  # already flown or airborne: debited from the picture at launch

            minutes = s.recover_min - s.launch_min
            aircraft_minutes[a.tail] += minutes
            crew_minutes[crew.id] += minutes
            crew_sorties[crew.id] += 1
            stock_use[(s.base_id, s.weapon_code)] += s.weapon_qty

            if a.status != AircraftStatus.SERVICEABLE:
                v.append(f"{label}: aircraft unserviceable")
            if prediction.base_unavailable_reason(cop.base(a.base_id), cop.weather):
                v.append(f"{label}: launch base unavailable")
            if m.type not in atype.roles:
                v.append(f"{label}: {atype.code} cannot fly {m.type}")
            if night and not (atype.night_capable and crew.night_qualified):
                v.append(f"{label}: night mission without night-capable aircraft and crew")
            weapon = cop.weapon(s.weapon_code)
            if weapon.kind not in MISSION_WEAPON_KINDS[m.type] or weapon.pk.get(m.target_class, 0.0) <= 0:
                v.append(f"{label}: {weapon.code} unsuitable for this mission")
            if not any(
                lo.weapon_code == weapon.code and lo.quantity == s.weapon_qty for lo in atype.loadouts
            ):
                v.append(f"{label}: loadout not cleared for {atype.code}")
            if target_wx == WeatherCondition.STORM or (
                target_wx == WeatherCondition.CLOUD and weapon.needs_clear_weather
            ):
                v.append(f"{label}: {weapon.code} unusable in target weather")
            limit = atype.combat_radius_km * (AAR_RADIUS_FACTOR if s.needs_aar else 1.0)
            if s.route_km > limit + RANGE_TOLERANCE_KM:
                v.append(f"{label}: route {s.route_km:.0f} km exceeds radius {limit:.0f} km")
            if crew.type_code != a.type_code or crew.base_id != a.base_id or not crew.available:
                v.append(f"{label}: crew {crew.id} not qualified, not at base or unavailable")

    for tail, uses in aircraft_use.items():
        v += [f"{tail}: overlapping taskings {a} and {b}" for a, b in _overlaps(uses)]
    for crew_id, uses in crew_use.items():
        v += [f"crew {crew_id}: overlapping taskings {a} and {b}" for a, b in _overlaps(uses)]
    for (base_id, weapon_code), used in stock_use.items():
        if used > cop.stock(base_id, weapon_code):
            v.append(f"{base_id}: plan needs {used} {weapon_code}, {cop.stock(base_id, weapon_code)} held")
    for tail, minutes in aircraft_minutes.items():
        if minutes > aircraft[tail].hours_to_maintenance * 60:
            v.append(f"{tail}: plan exceeds hours to scheduled maintenance")
    for crew_id, minutes in crew_minutes.items():
        crew = crews[crew_id]
        if crew.duty_min_used + minutes > CREW_MAX_DUTY_MIN:
            v.append(f"crew {crew_id}: exceeds flying-duty limit")
        if crew.sorties_today + crew_sorties[crew_id] > CREW_MAX_SORTIES_PER_DAY:
            v.append(f"crew {crew_id}: exceeds sorties-per-day limit")
    tankers = sum(
        1
        for a in cop.aircraft
        if cop.aircraft_type(a.type_code).is_tanker and a.status == AircraftStatus.SERVICEABLE
    )
    if aar_receivers > tankers * TANKER_RECEIVERS_PER_SORTIE * TANKER_SORTIES_PER_DAY:
        v.append(f"tanker support oversubscribed: {aar_receivers} receivers")
    return v
