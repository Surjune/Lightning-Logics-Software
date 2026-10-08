"""Situation changes injected into the picture, each attributed to its source system."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.core.geometry import Point
from app.domain.enums import BaseStatus, TargetClass, WeatherCondition
from app.domain.models import Priority, Probability, RadiusKm

ShortText = Annotated[str, Field(min_length=1, max_length=80)]
IdList = Annotated[list[Annotated[str, Field(min_length=1, max_length=20)]], Field(min_length=1, max_length=12)]


class AircraftUnserviceableEvent(BaseModel):
    kind: Literal["AIRCRAFT_UNSERVICEABLE"] = "AIRCRAFT_UNSERVICEABLE"
    tails: IdList
    reason: ShortText = "Defect raised on pre-flight inspection"


class AircraftRestoredEvent(BaseModel):
    kind: Literal["AIRCRAFT_RESTORED"] = "AIRCRAFT_RESTORED"
    tails: IdList


class CrewUnavailableEvent(BaseModel):
    kind: Literal["CREW_UNAVAILABLE"] = "CREW_UNAVAILABLE"
    crew_ids: IdList
    reason: ShortText = "Medically grounded"


class MunitionShortageEvent(BaseModel):
    kind: Literal["MUNITION_SHORTAGE"] = "MUNITION_SHORTAGE"
    base_id: Annotated[str, Field(min_length=1, max_length=20)]
    weapon_code: Annotated[str, Field(min_length=1, max_length=20)]
    quantity: Annotated[int, Field(ge=0, le=500)]
    reason: ShortText = "Lot quarantined after inspection"


class SamPopupEvent(BaseModel):
    kind: Literal["SAM_POPUP"] = "SAM_POPUP"
    name: ShortText = "Pop-up SAM"
    position: Point
    range_km: RadiusKm = 55.0
    lethality: Probability = 0.7


class BaseWeatherEvent(BaseModel):
    kind: Literal["BASE_WEATHER"] = "BASE_WEATHER"
    base_id: Annotated[str, Field(min_length=1, max_length=20)]
    status: BaseStatus


class WeatherCellEvent(BaseModel):
    kind: Literal["WEATHER_CELL"] = "WEATHER_CELL"
    centre: Point
    radius_km: RadiusKm = 60.0
    condition: WeatherCondition = WeatherCondition.CLOUD


class TimeSensitiveTargetEvent(BaseModel):
    kind: Literal["TIME_SENSITIVE_TARGET"] = "TIME_SENSITIVE_TARGET"
    name: ShortText = "Mobile missile launcher"
    target_class: TargetClass = TargetClass.MOBILE
    position: Point
    priority: Priority = 98
    opens_in_min: Annotated[int, Field(ge=0, le=240)] = 45
    window_min: Annotated[int, Field(ge=15, le=480)] = 90
    aircraft_required: Annotated[int, Field(ge=1, le=4)] = 2


class AirspaceClosureEvent(BaseModel):
    kind: Literal["AIRSPACE_CLOSURE"] = "AIRSPACE_CLOSURE"
    name: ShortText = "Temporary restricted area"
    centre: Point
    radius_km: RadiusKm = 40.0


SituationEvent = Annotated[
    AircraftUnserviceableEvent
    | AircraftRestoredEvent
    | CrewUnavailableEvent
    | MunitionShortageEvent
    | SamPopupEvent
    | BaseWeatherEvent
    | WeatherCellEvent
    | TimeSensitiveTargetEvent
    | AirspaceClosureEvent,
    Field(discriminator="kind"),
]


class EventPreset(BaseModel):
    """A ready-made event for the demo, resolved against the current plan."""

    id: str
    title: str
    source: str
    description: str
    event: SituationEvent
