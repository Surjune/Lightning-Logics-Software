"""Fuses situation reports into the picture and triggers retasking."""

from collections import Counter

from app.core.clock import fmt_clock
from app.core.constants import (
    HORIZON_MIN,
    POPUP_SAM_SEAD_PRIORITY,
    SAM_CORRELATION_KM,
    SEAD_PACKAGE_SIZE,
    SEAD_WINDOW_DELAY_MIN,
    SEAD_WINDOW_LENGTH_MIN,
    SINGLE_SOURCE_CONFIDENCE,
)
from app.core.exceptions import InvalidEventError
from app.core.geometry import Point, distance_km, is_hostile_side, point_along
from app.domain.enums import (
    AircraftStatus,
    BaseStatus,
    MissionOrigin,
    MissionType,
    PackageStatus,
    SourceSystem,
    TargetClass,
    WeatherCondition,
)
from app.domain.events import (
    AircraftRestoredEvent,
    AircraftUnserviceableEvent,
    AirspaceClosureEvent,
    BaseWeatherEvent,
    CrewUnavailableEvent,
    EventPreset,
    MunitionShortageEvent,
    SamPopupEvent,
    SituationEvent,
    TimeSensitiveTargetEvent,
    WeatherCellEvent,
)
from app.domain.models import Aircraft, AirspaceZone, Cop, FeedEntry, Mission, SamSite, WeatherCell
from app.domain.plan import Package, Proposal
from app.services.planning import PlanningService
from app.services.state import OpsState

# Demo preset geometry: where along a package's route to place a pop-up threat or an
# airspace closure (fraction of route length), and fall-back positions in each territory.
POPUP_ROUTE_FRACTION = 0.85
CLOSURE_ROUTE_FRACTION = 0.3
FALLBACK_HOSTILE_POINT = Point(x_km=620.0, y_km=420.0)
FALLBACK_FRIENDLY_POINT = Point(x_km=380.0, y_km=420.0)
TST_POSITION = Point(x_km=655.0, y_km=335.0)
PRESET_CLOSURE_RADIUS_KM = 35.0
PRESET_CLOUD_RADIUS_KM = 50.0
PRESET_SHORTAGE_QUANTITY = 0


class EventService:
    def __init__(self, state: OpsState, planning: PlanningService) -> None:
        self.state = state
        self.planning = planning

    # --- applying events -------------------------------------------------------------
    def apply(self, event: SituationEvent) -> tuple[list[FeedEntry], Proposal | None]:
        with self.state.lock:
            cop = self.state.cop
            feed_before = len(cop.feed)
            source, summary = self._apply(cop, event)
            self._report(cop, source, summary)
            self.state.audit.record(cop.clock_min, f"source:{source}", event.kind, summary)
            new_feed = cop.feed[feed_before:]
            proposal = self.planning.generate(summary) if self.state.plan.packages else None
            return new_feed, proposal

    def _report(self, cop: Cop, source: SourceSystem, summary: str) -> None:
        cop.feed.append(FeedEntry(at_min=cop.clock_min, source=source, summary=summary))
        for s in cop.sources:
            if s.source == source:
                s.last_update_min = cop.clock_min

    def _apply(self, cop: Cop, event: SituationEvent) -> tuple[SourceSystem, str]:
        match event:
            case AircraftUnserviceableEvent():
                aircraft = self._aircraft(cop, event.tails)
                for a in aircraft:
                    a.status = AircraftStatus.UNSERVICEABLE
                    a.status_note = event.reason
                return SourceSystem.MAINTENANCE, f"{', '.join(event.tails)} unserviceable: {event.reason}"
            case AircraftRestoredEvent():
                for a in self._aircraft(cop, event.tails):
                    a.status = AircraftStatus.SERVICEABLE
                    a.status_note = ""
                return SourceSystem.MAINTENANCE, f"{', '.join(event.tails)} returned to service"
            case CrewUnavailableEvent():
                crews = {c.id: c for c in cop.crews}
                missing = [k for k in event.crew_ids if k not in crews]
                if missing:
                    raise InvalidEventError(f"Unknown crew: {', '.join(missing)}")
                for k in event.crew_ids:
                    crews[k].available = False
                    crews[k].status_note = event.reason
                names = ", ".join(crews[k].callsign for k in event.crew_ids)
                return SourceSystem.CREW, f"{names} unavailable: {event.reason}"
            case MunitionShortageEvent():
                stock = next(
                    (
                        s
                        for s in cop.stocks
                        if s.base_id == event.base_id and s.weapon_code == event.weapon_code
                    ),
                    None,
                )
                if stock is None:
                    raise InvalidEventError(f"{event.base_id} holds no {event.weapon_code}")
                before = stock.quantity
                stock.quantity = event.quantity
                return (
                    SourceSystem.ARMAMENT,
                    f"{event.weapon_code} at {cop.base(event.base_id).name}: {before} -> {event.quantity} "
                    f"({event.reason})",
                )
            case SamPopupEvent():
                return SourceSystem.INTEL, self._fuse_sam(cop, event)
            case BaseWeatherEvent():
                base = next((b for b in cop.bases if b.id == event.base_id), None)
                if base is None:
                    raise InvalidEventError(f"Unknown base {event.base_id}")
                base.status = event.status
                summary = f"{base.name} {event.status.value.lower()}"
                if event.status == BaseStatus.CLOSED:
                    summary += " (thunderstorm below landing minima)"
                    airborne = [
                        p.mission_id
                        for p in self.state.plan.packages
                        if p.status == PackageStatus.AIRBORNE and any(s.base_id == base.id for s in p.sorties)
                    ]
                    if airborne:
                        summary += f"; airborne {', '.join(airborne)} to divert to the nearest open base"
                return SourceSystem.MET, summary
            case WeatherCellEvent():
                cell = WeatherCell(
                    id=f"W-{len(cop.weather) + 1:02d}",
                    centre=event.centre,
                    radius_km=event.radius_km,
                    condition=event.condition,
                    reported_at_min=cop.clock_min,
                )
                cop.weather.append(cell)
                return (
                    SourceSystem.MET,
                    f"{event.condition.value.title()} cell {cell.id} reported, "
                    f"radius {event.radius_km:.0f} km",
                )
            case TimeSensitiveTargetEvent():
                start = min(cop.clock_min + event.opens_in_min, HORIZON_MIN)
                mission = Mission(
                    id=self._next_mission_id(cop),
                    name=f"TST: {event.name}",
                    type=MissionType.STRIKE,
                    target_class=event.target_class,
                    position=event.position,
                    priority=event.priority,
                    window_start_min=start,
                    window_end_min=min(start + event.window_min, HORIZON_MIN),
                    aircraft_required=event.aircraft_required,
                    origin=MissionOrigin.TST,
                )
                cop.missions.append(mission)
                return (
                    SourceSystem.OPS,
                    f"Time-sensitive target {mission.id} '{event.name}', priority {event.priority}, "
                    f"window {fmt_clock(mission.window_start_min)}-{fmt_clock(mission.window_end_min)}",
                )
            case AirspaceClosureEvent():
                zone = AirspaceZone(
                    id=f"A-{len(cop.airspace) + 1:02d}",
                    name=event.name,
                    centre=event.centre,
                    radius_km=event.radius_km,
                    reported_at_min=cop.clock_min,
                )
                cop.airspace.append(zone)
                return (
                    SourceSystem.AIRSPACE,
                    f"{zone.id} {event.name} activated, radius {event.radius_km:.0f} km",
                )
        raise InvalidEventError(f"Unsupported event {event.kind}")

    @staticmethod
    def _aircraft(cop: Cop, tails: list[str]) -> list[Aircraft]:
        by_tail = {a.tail: a for a in cop.aircraft}
        missing = [t for t in tails if t not in by_tail]
        if missing:
            raise InvalidEventError(f"Unknown aircraft: {', '.join(missing)}")
        return [by_tail[t] for t in tails]

    @staticmethod
    def _next_mission_id(cop: Cop) -> str:
        return f"M-{max(int(m.id[2:]) for m in cop.missions) + 1:02d}"

    def _fuse_sam(self, cop: Cop, event: SamPopupEvent) -> str:
        """Correlate with a known site (same site, more confidence) or add a new one."""
        known = min(cop.sams, key=lambda s: distance_km(s.position, event.position), default=None)
        if known is not None and distance_km(known.position, event.position) <= SAM_CORRELATION_KM:
            before = known.confidence
            known.confidence = round(1 - (1 - known.confidence) * (1 - SINGLE_SOURCE_CONFIDENCE), 3)
            known.range_km = max(known.range_km, event.range_km)
            known.position = event.position
            known.reported_at_min = cop.clock_min
            known.active = True
            return (
                f"SAM report correlated with {known.id} {known.name}: confidence "
                f"{before:.2f} -> {known.confidence:.2f}"
            )
        site = SamSite(
            id=f"S-{len(cop.sams) + 1:02d}",
            name=event.name,
            position=event.position,
            range_km=event.range_km,
            lethality=event.lethality,
            confidence=SINGLE_SOURCE_CONFIDENCE,
            reported_at_min=cop.clock_min,
        )
        cop.sams.append(site)
        summary = f"New SAM {site.id} '{event.name}', range {event.range_km:.0f} km (single source)"
        start = cop.clock_min + SEAD_WINDOW_DELAY_MIN
        if is_hostile_side(event.position, cop.border) and start < HORIZON_MIN:
            sead = Mission(
                id=self._next_mission_id(cop),
                name=f"SEAD {site.id}",
                type=MissionType.SEAD,
                target_class=TargetClass.SAM,
                position=event.position,
                priority=POPUP_SAM_SEAD_PRIORITY,
                window_start_min=start,
                window_end_min=min(start + SEAD_WINDOW_LENGTH_MIN, HORIZON_MIN),
                aircraft_required=SEAD_PACKAGE_SIZE,
                origin=MissionOrigin.AUTO_SEAD,
                sam_id=site.id,
            )
            cop.missions.append(sead)
            summary += f"; SEAD tasking {sead.id} raised"
        return summary

    # --- demo presets --------------------------------------------------------------
    def presets(self) -> list[EventPreset]:
        with self.state.lock:
            cop = self.state.cop
            open_pkgs = sorted(
                (p for p in self.state.plan.packages if p.status == PackageStatus.PLANNED),
                key=lambda p: -cop.mission(p.mission_id).priority,
            )
            strike = [p for p in open_pkgs if cop.mission(p.mission_id).type == MissionType.STRIKE]
            presets: list[EventPreset] = []

            if open_pkgs:
                lead = open_pkgs[0]
                tails = [s.aircraft_tail for s in lead.sorties[:2]]
                crews = [s.crew_id for s in lead.sorties[:2]]
                presets.append(
                    EventPreset(
                        id="ground",
                        title=f"Ground {' & '.join(tails)}",
                        source="Maintenance",
                        description=f"Hydraulic defect found on aircraft tasked for {lead.mission_id} "
                        f"{cop.mission(lead.mission_id).name}.",
                        event=AircraftUnserviceableEvent(tails=tails, reason="Hydraulic leak on pre-flight"),
                    )
                )
                presets.append(
                    EventPreset(
                        id="crew",
                        title="Ground two crews",
                        source="Crew",
                        description=f"Crews tasked for {lead.mission_id} medically grounded.",
                        event=CrewUnavailableEvent(crew_ids=crews, reason="Medically grounded"),
                    )
                )
            else:
                serviceable = [a.tail for a in cop.aircraft if a.status == AircraftStatus.SERVICEABLE][:2]
                presets.append(
                    EventPreset(
                        id="ground",
                        title=f"Ground {' & '.join(serviceable)}",
                        source="Maintenance",
                        description="Hydraulic defect found on pre-flight inspection.",
                        event=AircraftUnserviceableEvent(
                            tails=serviceable, reason="Hydraulic leak on pre-flight"
                        ),
                    )
                )

            presets.append(
                EventPreset(
                    id="sam",
                    title="Pop-up SAM on a planned route",
                    source="Intelligence",
                    description="A previously unknown medium-range SAM is detected on the ingress "
                    "route of the highest-priority strike.",
                    event=SamPopupEvent(
                        name="Pop-up SAM 'Ghost'",
                        position=self._route_point(cop, strike, hostile=True),
                        range_km=55.0,
                        lethality=0.7,
                    ),
                )
            )
            presets.append(
                EventPreset(
                    id="tst",
                    title="Time-sensitive target: mobile launcher",
                    source="Ops",
                    description="Mobile missile launcher located; it will relocate within about 90 minutes.",
                    event=TimeSensitiveTargetEvent(position=TST_POSITION),
                )
            )
            affected = self._least_busy_base(cop, open_pkgs)
            presets.append(
                EventPreset(
                    id="base",
                    title=f"Thunderstorm closes {cop.base(affected).name}",
                    source="Met",
                    description="Thunderstorm over the airfield; below take-off and landing minima.",
                    event=BaseWeatherEvent(base_id=affected, status=BaseStatus.CLOSED),
                )
            )
            presets.append(
                EventPreset(
                    id="airspace",
                    title="Restricted airspace activated",
                    source="Airspace",
                    description="Civil emergency: temporary restricted area across a planned ingress route.",
                    event=AirspaceClosureEvent(
                        name="Temporary restricted area (civil emergency)",
                        centre=self._route_point(cop, open_pkgs, hostile=False),
                        radius_km=PRESET_CLOSURE_RADIUS_KM,
                    ),
                )
            )
            used = Counter(
                (s.base_id, s.weapon_code) for p in open_pkgs for s in p.sorties if s.weapon_code != "AAM"
            )
            if used:
                (base_id, weapon_code), _ = used.most_common(1)[0]
                presets.append(
                    EventPreset(
                        id="munitions",
                        title=f"{weapon_code} lot quarantined at {cop.base(base_id).name}",
                        source="Armament",
                        description="Inspection finds a defect across the lot; all rounds withdrawn.",
                        event=MunitionShortageEvent(
                            base_id=base_id, weapon_code=weapon_code, quantity=PRESET_SHORTAGE_QUANTITY
                        ),
                    )
                )
            cloud_target = next(
                (
                    cop.mission(p.mission_id).position
                    for p in strike
                    if any(cop.weapon(s.weapon_code).needs_clear_weather for s in p.sorties)
                ),
                None,
            )
            if cloud_target is not None:
                presets.append(
                    EventPreset(
                        id="cloud",
                        title="Low cloud over a strike target",
                        source="Met",
                        description="Low cloud rolls in over a target planned for laser-guided weapons.",
                        event=WeatherCellEvent(
                            centre=cloud_target,
                            radius_km=PRESET_CLOUD_RADIUS_KM,
                            condition=WeatherCondition.CLOUD,
                        ),
                    )
                )
            return presets

    @staticmethod
    def _route_point(cop: Cop, packages: list[Package], hostile: bool) -> Point:
        fraction = POPUP_ROUTE_FRACTION if hostile else CLOSURE_ROUTE_FRACTION
        for p in packages:
            route = p.sorties[0].route
            if len(route) < 2:
                continue
            point = point_along(route, fraction)
            if is_hostile_side(point, cop.border) == hostile:
                return point
        return FALLBACK_HOSTILE_POINT if hostile else FALLBACK_FRIENDLY_POINT

    @staticmethod
    def _least_busy_base(cop: Cop, packages: list[Package]) -> str:
        """The base with the fewest planned sorties that still has some: a closure that
        forces a visible retask without wiping out a large share of the plan."""
        counts = Counter(s.base_id for p in packages for s in p.sorties)
        return min(counts, key=lambda b: (counts[b], b)) if counts else cop.bases[0].id
