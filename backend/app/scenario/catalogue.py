"""Fictional platform and weapon catalogue.

Figures are rounded, class-representative values from open-source literature, chosen so
that each platform has a distinct trade-off (reach vs survivability vs weapon options).
They are not the specification of any real aircraft or weapon.
"""

from app.domain.enums import MissionType, TargetClass, WeaponKind
from app.domain.models import AircraftType, Loadout, Weapon

T = TargetClass

WEAPONS: list[Weapon] = [
    Weapon(
        code="SOW",
        name="Standoff cruise missile",
        kind=WeaponKind.STANDOFF,
        standoff_km=150.0,
        needs_clear_weather=False,
        scarcity=12.0,  # few rounds held theatre-wide; expend only on high-value targets
        pk={
            T.AIRFIELD: 0.6,
            T.RADAR: 0.8,
            T.SAM: 0.7,
            T.BRIDGE: 0.75,
            T.DEPOT: 0.85,
            T.COMMAND: 0.85,
            T.ARMOUR: 0.2,
            T.MOBILE: 0.3,
        },
    ),
    Weapon(
        code="LGB",
        name="Laser-guided bomb",
        kind=WeaponKind.LGB,
        standoff_km=10.0,
        needs_clear_weather=True,
        scarcity=1.5,
        pk={
            T.AIRFIELD: 0.55,
            T.RADAR: 0.7,
            T.SAM: 0.6,
            T.BRIDGE: 0.8,
            T.DEPOT: 0.75,
            T.COMMAND: 0.7,
            T.ARMOUR: 0.55,
            T.MOBILE: 0.6,
        },
    ),
    Weapon(
        code="GPS",
        name="Satellite-guided glide bomb",
        kind=WeaponKind.GPS,
        standoff_km=40.0,
        needs_clear_weather=False,
        scarcity=2.5,
        pk={
            T.AIRFIELD: 0.5,
            T.RADAR: 0.55,
            T.SAM: 0.5,
            T.BRIDGE: 0.6,
            T.DEPOT: 0.65,
            T.COMMAND: 0.6,
            T.ARMOUR: 0.25,
            T.MOBILE: 0.15,
        },
    ),
    Weapon(
        code="ARM",
        name="Anti-radiation missile",
        kind=WeaponKind.ARM,
        standoff_km=70.0,
        needs_clear_weather=False,
        scarcity=4.0,
        pk={T.RADAR: 0.75, T.SAM: 0.7},
    ),
    Weapon(
        code="AAM",
        name="Beyond-visual-range air-to-air missile",
        kind=WeaponKind.AAM,
        standoff_km=0.0,
        needs_clear_weather=False,
        scarcity=0.5,
        pk={T.CAP_STATION: 1.0},  # DCA effect is set by the platform's air-to-air rating
    ),
    Weapon(
        code="UGB",
        name="Unguided bomb",
        kind=WeaponKind.UNGUIDED,
        standoff_km=0.0,
        needs_clear_weather=True,  # visual delivery
        scarcity=0.1,
        pk={
            T.AIRFIELD: 0.25,
            T.RADAR: 0.2,
            T.SAM: 0.15,
            T.BRIDGE: 0.2,
            T.DEPOT: 0.3,
            T.COMMAND: 0.15,
            T.ARMOUR: 0.35,
            T.MOBILE: 0.3,
        },
    ),
]

# Which weapon kinds each mission type may employ.
MISSION_WEAPON_KINDS: dict[MissionType, set[WeaponKind]] = {
    MissionType.STRIKE: {WeaponKind.STANDOFF, WeaponKind.LGB, WeaponKind.GPS, WeaponKind.UNGUIDED},
    MissionType.SEAD: {WeaponKind.ARM, WeaponKind.STANDOFF},
    MissionType.DCA: {WeaponKind.AAM},
}

AIRCRAFT_TYPES: list[AircraftType] = [
    AircraftType(
        code="OMF",
        name="Omni-role fighter",
        roles=[MissionType.STRIKE, MissionType.SEAD, MissionType.DCA],
        cruise_kmh=900.0,
        combat_radius_km=750.0,
        survivability=0.70,
        air_to_air=0.90,
        turnaround_min=50,
        night_capable=True,
        loadouts=[
            Loadout(weapon_code="SOW", quantity=1),
            Loadout(weapon_code="LGB", quantity=2),
            Loadout(weapon_code="GPS", quantity=2),
            Loadout(weapon_code="ARM", quantity=2),
            Loadout(weapon_code="AAM", quantity=6),
        ],
    ),
    AircraftType(
        code="MRF",
        name="Heavy multi-role fighter",
        roles=[MissionType.STRIKE, MissionType.SEAD, MissionType.DCA],
        cruise_kmh=850.0,
        combat_radius_km=650.0,
        survivability=0.60,
        air_to_air=0.85,
        turnaround_min=60,
        night_capable=True,
        loadouts=[
            Loadout(weapon_code="SOW", quantity=1),
            Loadout(weapon_code="LGB", quantity=2),
            Loadout(weapon_code="GPS", quantity=4),
            Loadout(weapon_code="ARM", quantity=2),
            Loadout(weapon_code="AAM", quantity=8),
        ],
    ),
    AircraftType(
        code="LCF",
        name="Light combat fighter",
        roles=[MissionType.STRIKE, MissionType.DCA],
        cruise_kmh=800.0,
        combat_radius_km=450.0,
        survivability=0.50,
        air_to_air=0.75,
        turnaround_min=40,
        night_capable=False,
        loadouts=[
            Loadout(weapon_code="GPS", quantity=2),
            Loadout(weapon_code="UGB", quantity=4),
            Loadout(weapon_code="AAM", quantity=4),
        ],
    ),
    AircraftType(
        code="DPS",
        name="Deep-penetration striker",
        roles=[MissionType.STRIKE],
        cruise_kmh=800.0,
        combat_radius_km=600.0,
        survivability=0.45,
        air_to_air=0.30,
        turnaround_min=70,
        night_capable=True,
        loadouts=[
            Loadout(weapon_code="LGB", quantity=2),
            Loadout(weapon_code="GPS", quantity=2),
            Loadout(weapon_code="UGB", quantity=6),
        ],
    ),
    AircraftType(
        code="TKR",
        name="Air-to-air refueller",
        roles=[],
        is_tanker=True,
        cruise_kmh=750.0,
        combat_radius_km=1500.0,
        survivability=0.20,
        air_to_air=0.0,
        turnaround_min=90,
        night_capable=True,
        loadouts=[],
    ),
]
