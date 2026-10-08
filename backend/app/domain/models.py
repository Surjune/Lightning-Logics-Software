"""The Common Operating Picture: every fact the planner reasons over.

Each fused fact carries the source system that reported it and the scenario minute
it was reported, so the picture can show data age and confidence.
"""

from typing import Annotated

from pydantic import BaseModel, Field

from app.core.constants import HORIZON_MIN
from app.core.geometry import Point
from app.domain.enums import (
    AircraftStatus,
    BaseStatus,
    MissionOrigin,
    MissionType,
    SourceSystem,
    TargetClass,
    WeaponKind,
    WeatherCondition,
)

Probability = Annotated[float, Field(ge=0.0, le=1.0)]
Minute = Annotated[int, Field(ge=0, le=HORIZON_MIN)]
Priority = Annotated[int, Field(ge=1, le=100)]
RadiusKm = Annotated[float, Field(gt=0.0, le=400.0)]


class Loadout(BaseModel):
    weapon_code: str
    quantity: Annotated[int, Field(ge=1, le=12)]


class AircraftType(BaseModel):
    code: str
    name: str
    roles: list[MissionType]
    is_tanker: bool = False
    cruise_kmh: Annotated[float, Field(gt=0.0)]
    combat_radius_km: Annotated[float, Field(gt=0.0)]
    survivability: Probability
    air_to_air: Probability
    turnaround_min: Annotated[int, Field(ge=0)]
    night_capable: bool
    loadouts: list[Loadout]


class Weapon(BaseModel):
    code: str
    name: str
    kind: WeaponKind
    standoff_km: Annotated[float, Field(ge=0.0)]
    needs_clear_weather: bool
    # Relative cost of expending one round, in priority points; high for scarce stores.
    scarcity: Annotated[float, Field(ge=0.0)]
    pk: dict[TargetClass, Probability]


class AirBase(BaseModel):
    id: str
    name: str
    position: Point
    status: BaseStatus = BaseStatus.OPEN


class Aircraft(BaseModel):
    tail: str
    type_code: str
    base_id: str
    status: AircraftStatus = AircraftStatus.SERVICEABLE
    status_note: str = ""
    hours_to_maintenance: Annotated[float, Field(ge=0.0)]
    sorties_today: Annotated[int, Field(ge=0)] = 0
    snags_30d: Annotated[int, Field(ge=0)] = 0


class Crew(BaseModel):
    id: str
    callsign: str
    type_code: str
    base_id: str
    night_qualified: bool
    available: bool = True
    status_note: str = ""
    duty_min_used: Annotated[int, Field(ge=0)] = 0
    sorties_today: Annotated[int, Field(ge=0)] = 0


class WeaponStock(BaseModel):
    base_id: str
    weapon_code: str
    quantity: Annotated[int, Field(ge=0)]


class Mission(BaseModel):
    id: str
    name: str
    type: MissionType
    target_class: TargetClass
    position: Point
    priority: Priority
    window_start_min: Minute
    window_end_min: Minute
    aircraft_required: Annotated[int, Field(ge=1, le=8)]
    on_station_min: Annotated[int, Field(ge=0)] = 0
    origin: MissionOrigin = MissionOrigin.ATO
    sam_id: str | None = None  # SEAD missions: the SAM site they suppress


class SamSite(BaseModel):
    id: str
    name: str
    position: Point
    range_km: RadiusKm
    lethality: Probability
    active: bool = True
    confidence: Probability = 1.0
    reported_at_min: Minute = 0
    source: SourceSystem = SourceSystem.INTEL


class WeatherCell(BaseModel):
    id: str
    centre: Point
    radius_km: RadiusKm
    condition: WeatherCondition
    reported_at_min: Minute = 0


class AirspaceZone(BaseModel):
    id: str
    name: str
    centre: Point
    radius_km: RadiusKm
    active: bool = True
    reported_at_min: Minute = 0


class SourceStatus(BaseModel):
    source: SourceSystem
    system_name: str
    last_update_min: Minute
    records: Annotated[int, Field(ge=0)]


class FeedEntry(BaseModel):
    """One report received from a source system, as shown in the fusion feed."""

    at_min: Minute
    source: SourceSystem
    summary: str


class Cop(BaseModel):
    scenario_name: str
    seed: int
    clock_min: Minute = 0
    border: list[Point]
    aircraft_types: list[AircraftType]
    weapons: list[Weapon]
    bases: list[AirBase]
    aircraft: list[Aircraft]
    crews: list[Crew]
    stocks: list[WeaponStock]
    missions: list[Mission]
    sams: list[SamSite]
    weather: list[WeatherCell]
    airspace: list[AirspaceZone]
    sources: list[SourceStatus]
    feed: list[FeedEntry] = Field(default_factory=list)

    # Lookup helpers. The COP is small (tens to hundreds of records) so linear scans
    # keep the model a plain serialisable document with no derived indexes to drift.
    def aircraft_type(self, code: str) -> AircraftType:
        return next(t for t in self.aircraft_types if t.code == code)

    def weapon(self, code: str) -> Weapon:
        return next(w for w in self.weapons if w.code == code)

    def base(self, base_id: str) -> AirBase:
        return next(b for b in self.bases if b.id == base_id)

    def mission(self, mission_id: str) -> Mission:
        return next(m for m in self.missions if m.id == mission_id)

    def stock(self, base_id: str, weapon_code: str) -> int:
        return next(
            (s.quantity for s in self.stocks if s.base_id == base_id and s.weapon_code == weapon_code),
            0,
        )
