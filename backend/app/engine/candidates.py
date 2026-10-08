"""Candidate generation: every feasible (mission, aircraft, loadout) option, pre-scored.

Filtering infeasible combinations here, before the solver sees them, shrinks the search
space by an order of magnitude. Every rejection is tallied with a reason so the
explanation layer can say exactly why a mission went unresourced.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from app.core.constants import (
    AAR_RADIUS_FACTOR,
    ARMING_LEAD_TIME_MIN,
    CREW_FATIGUE_LIMIT,
    CREW_MAX_DUTY_MIN,
    CREW_MAX_SORTIES_PER_DAY,
    CREW_MIN_REST_MIN,
    FORM_UP_MIN,
    HORIZON_MIN,
    SEAD_TIME_ON_TARGET_MIN,
    SERVICEABILITY_PLANNING_FLOOR,
    STRIKE_TIME_ON_TARGET_MIN,
    TANKER_RECEIVERS_PER_SORTIE,
    TANKER_SORTIES_PER_DAY,
)
from app.core.geometry import Point
from app.domain.enums import AircraftStatus, MissionType, PackageStatus, RejectReason, WeatherCondition
from app.domain.models import Cop, Mission
from app.domain.plan import Package
from app.engine import prediction
from app.engine.routing import Router
from app.scenario.catalogue import MISSION_WEAPON_KINDS


@dataclass(frozen=True)
class Candidate:
    idx: int
    mission_id: str
    tail: str
    type_code: str
    base_id: str
    weapon_code: str
    qty: int
    pre_min: int  # launch to time on target
    post_min: int  # time on target to recovery
    turnaround_min: int
    earliest_tot: int
    latest_tot: int
    route: tuple[Point, ...]
    route_km: float
    exposure_km: float
    risk: float
    effect: float
    p_serv: float
    p_success: float
    needs_aar: bool
    scarcity_cost: float
    value: float  # priority points this aircraft contributes if its package flies

    @property
    def duration_min(self) -> int:
        return self.pre_min + self.post_min


@dataclass
class Commitments:
    """Resources already spoken for by packages the retasker must not touch."""

    aircraft_busy: dict[str, list[tuple[int, int]]] = field(default_factory=lambda: defaultdict(list))
    crew_busy: dict[str, list[tuple[int, int]]] = field(default_factory=lambda: defaultdict(list))
    aircraft_minutes: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    crew_minutes: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    crew_sorties: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    stock_reserved: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))
    tanker_used: int = 0


def commitments_from(cop: Cop, frozen: list[Package]) -> Commitments:
    c = Commitments()
    for pkg in frozen:
        for s in pkg.sorties:
            turnaround = cop.aircraft_type(s.type_code).turnaround_min
            c.aircraft_busy[s.aircraft_tail].append((s.launch_min, s.recover_min + turnaround))
            c.crew_busy[s.crew_id].append((s.launch_min, s.recover_min + CREW_MIN_REST_MIN))
            if s.needs_aar:
                c.tanker_used += 1
            if pkg.status == PackageStatus.COMMITTED:
                # Airborne sorties were already debited from the picture at launch.
                minutes = s.recover_min - s.launch_min
                c.aircraft_minutes[s.aircraft_tail] += minutes
                c.crew_minutes[s.crew_id] += minutes
                c.crew_sorties[s.crew_id] += 1
                c.stock_reserved[(s.base_id, s.weapon_code)] += s.weapon_qty
    return c


@dataclass
class CandidateSet:
    candidates: list[Candidate]
    by_mission: dict[str, list[Candidate]]
    # mission id -> (base id, type code) -> eligible crew ids
    crews: dict[str, dict[tuple[str, str], list[str]]]
    crew_duty_left: dict[str, int]
    crew_sorties_left: dict[str, int]
    aircraft_minutes_left: dict[str, int]
    stock_left: dict[tuple[str, str], int]
    tanker_capacity: int
    rejections: dict[str, dict[RejectReason, tuple[int, str]]]

    def reject_count(self, mission_id: str) -> int:
        return sum(n for n, _ in self.rejections.get(mission_id, {}).values())


_LOADOUT_STAGE = [
    RejectReason.NO_SUITABLE_WEAPON,
    RejectReason.NO_WEAPON_STOCK,
    RejectReason.TARGET_WEATHER,
    RejectReason.OUT_OF_RANGE,
    RejectReason.TIME_WINDOW,
    RejectReason.MAINTENANCE_DUE,
    RejectReason.NO_QUALIFIED_CREW,
]


def _on_target_min(m: Mission) -> int:
    if m.type == MissionType.DCA:
        return m.on_station_min
    return SEAD_TIME_ON_TARGET_MIN if m.type == MissionType.SEAD else STRIKE_TIME_ON_TARGET_MIN


def generate_candidates(
    cop: Cop, router: Router, missions: list[Mission], now: int, commitments: Commitments
) -> CandidateSet:
    rejections: dict[str, dict[RejectReason, tuple[int, str]]] = defaultdict(dict)

    def reject(mission_id: str, reason: RejectReason, example: str) -> None:
        count, first = rejections[mission_id].get(reason, (0, example))
        rejections[mission_id][reason] = (count + 1, first)

    stock_left = {
        (s.base_id, s.weapon_code): s.quantity - commitments.stock_reserved[(s.base_id, s.weapon_code)]
        for s in cop.stocks
    }
    aircraft_minutes_left = {
        a.tail: int(a.hours_to_maintenance * 60) - commitments.aircraft_minutes[a.tail] for a in cop.aircraft
    }
    crew_duty_left = {
        c.id: CREW_MAX_DUTY_MIN - c.duty_min_used - commitments.crew_minutes[c.id] for c in cop.crews
    }
    crew_sorties_left = {
        c.id: CREW_MAX_SORTIES_PER_DAY - c.sorties_today - commitments.crew_sorties[c.id] for c in cop.crews
    }
    tankers = [
        a
        for a in cop.aircraft
        if cop.aircraft_type(a.type_code).is_tanker
        and a.status == AircraftStatus.SERVICEABLE
        and prediction.base_unavailable_reason(cop.base(a.base_id), cop.weather) is None
    ]
    tanker_capacity = max(
        len(tankers) * TANKER_RECEIVERS_PER_SORTIE * TANKER_SORTIES_PER_DAY - commitments.tanker_used, 0
    )
    base_reason = {b.id: prediction.base_unavailable_reason(b, cop.weather) for b in cop.bases}

    candidates: list[Candidate] = []
    by_mission: dict[str, list[Candidate]] = defaultdict(list)
    crews_for: dict[str, dict[tuple[str, str], list[str]]] = {}

    for m in missions:
        night = prediction.overlaps_night(m.window_start_min, m.window_end_min)
        target_wx = prediction.weather_at(m.position, cop.weather)
        crews_for[m.id] = defaultdict(list)
        for crew in cop.crews:
            if (
                crew.available
                and (crew.night_qualified or not night)
                and crew_sorties_left[crew.id] > 0
                and prediction.crew_fatigue(crew, night) < CREW_FATIGUE_LIMIT
            ):
                crews_for[m.id][(crew.base_id, crew.type_code)].append(crew.id)

        for a in cop.aircraft:
            atype = cop.aircraft_type(a.type_code)
            if atype.is_tanker or m.type not in atype.roles:
                continue
            if a.status == AircraftStatus.UNSERVICEABLE:
                reject(
                    m.id, RejectReason.AIRCRAFT_UNSERVICEABLE, f"{a.tail}: {a.status_note or 'unserviceable'}"
                )
                continue
            p_serv = prediction.serviceability(a)
            if p_serv < SERVICEABILITY_PLANNING_FLOOR:
                reject(
                    m.id, RejectReason.SERVICEABILITY_RISK, f"{a.tail}: predicted serviceability {p_serv:.0%}"
                )
                continue
            if base_reason[a.base_id]:
                reject(m.id, RejectReason.BASE_UNAVAILABLE, f"{a.tail}: {base_reason[a.base_id]}")
                continue
            if night and not atype.night_capable:
                reject(m.id, RejectReason.NOT_NIGHT_CAPABLE, f"{a.tail}: {atype.name} not night-capable")
                continue
            pool = crews_for[m.id].get((a.base_id, a.type_code), [])
            if not pool:
                reject(
                    m.id, RejectReason.NO_QUALIFIED_CREW, f"{a.tail}: no rested, qualified {a.type_code} crew"
                )
                continue
            profile = router.route(cop.base(a.base_id).position, m.position)
            if not profile.reachable:
                reject(
                    m.id,
                    RejectReason.ROUTE_BLOCKED,
                    f"{a.tail}: no route clear of storms/restricted airspace",
                )
                continue

            misses: list[tuple[RejectReason, str]] = []
            produced = False
            for lo in atype.loadouts:
                weapon = cop.weapon(lo.weapon_code)
                if (
                    weapon.kind not in MISSION_WEAPON_KINDS[m.type]
                    or weapon.pk.get(m.target_class, 0.0) <= 0.0
                ):
                    misses.append(
                        (RejectReason.NO_SUITABLE_WEAPON, f"{a.tail}: {weapon.name} ineffective here")
                    )
                    continue
                if stock_left.get((a.base_id, weapon.code), 0) < lo.quantity:
                    misses.append(
                        (RejectReason.NO_WEAPON_STOCK, f"{a.tail}: no {weapon.code} left at {a.base_id}")
                    )
                    continue
                if target_wx == WeatherCondition.STORM or (
                    target_wx == WeatherCondition.CLOUD and weapon.needs_clear_weather
                ):
                    misses.append(
                        (RejectReason.TARGET_WEATHER, f"{a.tail}: {weapon.code} unusable, target {target_wx}")
                    )
                    continue
                leg = profile.to_release(weapon.standoff_km)
                needs_aar = False
                if leg.km > atype.combat_radius_km:
                    if leg.km <= atype.combat_radius_km * AAR_RADIUS_FACTOR and tanker_capacity > 0:
                        needs_aar = True
                    else:
                        misses.append(
                            (
                                RejectReason.OUT_OF_RANGE,
                                f"{a.tail}: {leg.km:.0f} km vs radius {atype.combat_radius_km:.0f} km",
                            )
                        )
                        continue
                transit = round(leg.km / atype.cruise_kmh * 60)
                pre = FORM_UP_MIN + transit
                post = _on_target_min(m) + transit
                earliest = max(m.window_start_min, now + ARMING_LEAD_TIME_MIN + pre)
                latest = min(m.window_end_min, HORIZON_MIN - post)
                if earliest > latest:
                    misses.append(
                        (RejectReason.TIME_WINDOW, f"{a.tail}: cannot reach target inside its window")
                    )
                    continue
                if pre + post > aircraft_minutes_left[a.tail]:
                    misses.append(
                        (
                            RejectReason.MAINTENANCE_DUE,
                            f"{a.tail}: {a.hours_to_maintenance:.1f} h to scheduled maintenance",
                        )
                    )
                    continue
                if not any(crew_duty_left[c] >= pre + post for c in pool):
                    misses.append((RejectReason.NO_QUALIFIED_CREW, f"{a.tail}: no crew with duty time left"))
                    continue

                exposure = 2 * leg.exposure_km  # egress retraces the ingress route
                risk = prediction.loss_risk(exposure, atype.survivability)
                effect = (
                    atype.air_to_air
                    if m.type == MissionType.DCA
                    else prediction.weapon_effect(weapon, m.target_class, lo.quantity)
                )
                p_success = effect * p_serv * (1.0 - risk)
                cand = Candidate(
                    idx=len(candidates),
                    mission_id=m.id,
                    tail=a.tail,
                    type_code=a.type_code,
                    base_id=a.base_id,
                    weapon_code=weapon.code,
                    qty=lo.quantity,
                    pre_min=pre,
                    post_min=post,
                    turnaround_min=atype.turnaround_min,
                    earliest_tot=earliest,
                    latest_tot=latest,
                    route=leg.points,
                    route_km=leg.km,
                    exposure_km=exposure,
                    risk=risk,
                    effect=effect,
                    p_serv=p_serv,
                    p_success=p_success,
                    needs_aar=needs_aar,
                    scarcity_cost=weapon.scarcity * lo.quantity,
                    value=m.priority * p_success / m.aircraft_required,
                )
                candidates.append(cand)
                by_mission[m.id].append(cand)
                produced = True

            if not produced and misses:
                # Report the loadout that came closest to feasible: the latest check it failed.
                reason, example = max(misses, key=lambda r: _LOADOUT_STAGE.index(r[0]))
                reject(m.id, reason, example)

    return CandidateSet(
        candidates=candidates,
        by_mission=by_mission,
        crews=crews_for,
        crew_duty_left=crew_duty_left,
        crew_sorties_left=crew_sorties_left,
        aircraft_minutes_left=aircraft_minutes_left,
        stock_left=stock_left,
        tanker_capacity=tanker_capacity,
        rejections=rejections,
    )
