"""Predictive estimates that feed the optimiser.

Each is a small, transparent model with its parameters in core/constants.py. With real
maintenance, crew and BDA records these are the functions to replace with trained
models; their signatures are what the rest of the engine depends on.
"""

import math

from app.core.constants import (
    BASE_SERVICEABILITY,
    CREW_MAX_DUTY_MIN,
    LOW_HOURS_PENALTY,
    LOW_HOURS_THRESHOLD,
    MAX_SERVICEABILITY,
    MIN_SERVICEABILITY,
    NIGHT_END_MIN,
    NIGHT_FATIGUE_PENALTY,
    NIGHT_START_MIN,
    RISK_PER_EXPOSURE_KM,
    SERVICEABILITY_PENALTY_PER_SNAG,
    SERVICEABILITY_PENALTY_PER_SORTIE_TODAY,
)
from app.core.geometry import Point, in_circle
from app.domain.enums import AircraftStatus, BaseStatus, TargetClass, WeatherCondition
from app.domain.models import AirBase, Aircraft, Crew, Weapon, WeatherCell

_SEVERITY = {WeatherCondition.CLEAR: 0, WeatherCondition.CLOUD: 1, WeatherCondition.STORM: 2}


def serviceability(aircraft: Aircraft) -> float:
    """Probability the aircraft is still serviceable at its next launch."""
    if aircraft.status == AircraftStatus.UNSERVICEABLE:
        return 0.0
    p = (
        BASE_SERVICEABILITY
        - SERVICEABILITY_PENALTY_PER_SNAG * aircraft.snags_30d
        - SERVICEABILITY_PENALTY_PER_SORTIE_TODAY * aircraft.sorties_today
    )
    if aircraft.hours_to_maintenance < LOW_HOURS_THRESHOLD:
        p -= LOW_HOURS_PENALTY
    return min(max(p, MIN_SERVICEABILITY), MAX_SERVICEABILITY)


def overlaps_night(start_min: int, end_min: int) -> bool:
    return start_min < NIGHT_END_MIN or end_min > NIGHT_START_MIN


def crew_fatigue(crew: Crew, night: bool) -> float:
    """0 = fresh; 1 = duty limit reached. Night flying adds a circadian penalty."""
    return crew.duty_min_used / CREW_MAX_DUTY_MIN + (NIGHT_FATIGUE_PENALTY if night else 0.0)


def weather_at(p: Point, cells: list[WeatherCell]) -> WeatherCondition:
    worst = WeatherCondition.CLEAR
    for c in cells:
        if in_circle(p, c.centre, c.radius_km) and _SEVERITY[c.condition] > _SEVERITY[worst]:
            worst = c.condition
    return worst


def base_unavailable_reason(base: AirBase, cells: list[WeatherCell]) -> str | None:
    if base.status == BaseStatus.CLOSED:
        return f"{base.name} closed"
    if weather_at(base.position, cells) == WeatherCondition.STORM:
        return f"{base.name} below weather minima (thunderstorm)"
    return None


def weapon_effect(weapon: Weapon, target: TargetClass, quantity: int) -> float:
    """Probability at least one of `quantity` independent rounds achieves the effect."""
    pk = weapon.pk.get(target, 0.0)
    return 1.0 - (1.0 - pk) ** quantity


def loss_risk(exposure_km: float, survivability: float) -> float:
    """Probability of losing the aircraft given lethality-weighted km inside SAM cover."""
    return 1.0 - math.exp(-RISK_PER_EXPOSURE_KM * exposure_km * (1.0 - survivability))
