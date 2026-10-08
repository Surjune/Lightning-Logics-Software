"""Builds the fictional Blueland-vs-Redland scenario.

Geography, targets and threats are hand-placed so the demo has meaningful trade-offs.
Per-asset state (hours to maintenance, defects, crew duty) is drawn from a seeded RNG,
so a given seed always produces the identical picture.
"""

import math
import random

from app.core.geometry import Point
from app.domain.enums import (
    AircraftStatus,
    MissionType,
    SourceSystem,
    TargetClass,
    WeatherCondition,
)
from app.domain.models import (
    AirBase,
    Aircraft,
    AirspaceZone,
    Cop,
    Crew,
    FeedEntry,
    Mission,
    SamSite,
    SourceStatus,
    WeaponStock,
    WeatherCell,
)
from app.scenario.catalogue import AIRCRAFT_TYPES, WEAPONS

SCENARIO_NAME = "Exercise LIGHTNING SHIELD (fictional)"

# Squadron establishment ratio: crews held per aircraft on the line. One crew per
# aircraft with two sorties per crew per day makes crews, not airframes, the binding
# constraint on the surge day, which is the situation the planner must manage.
CREWS_PER_AIRCRAFT = 1.0
NIGHT_QUALIFIED_FRACTION = 0.7
HOURS_TO_MAINTENANCE_RANGE = (3.0, 40.0)
MAX_SNAGS_30D = 4
# Aircraft that start the day unserviceable, as (base id, type code, ordinal).
INITIAL_UNSERVICEABLE = [("B-ALP", "MRF", 3), ("B-BRV", "DPS", 2), ("B-CHL", "LCF", 5)]


def _p(x: float, y: float) -> Point:
    return Point(x_km=x, y_km=y)


BORDER = [
    _p(520, 700),
    _p(540, 600),
    _p(505, 500),
    _p(530, 400),
    _p(500, 300),
    _p(535, 200),
    _p(515, 100),
    _p(540, 0),
]

BASES = [
    AirBase(id="B-ALP", name="Alpha AB", position=_p(200, 560)),
    AirBase(id="B-BRV", name="Bravo AB", position=_p(300, 330)),
    AirBase(id="B-CHL", name="Charlie AB", position=_p(410, 160)),
    AirBase(id="B-DLT", name="Delta AB", position=_p(110, 250)),
]

# (base id, type code, count, crew callsign stem)
FLEET = [
    ("B-ALP", "OMF", 6, "Cobra"),
    ("B-ALP", "MRF", 6, "Viper"),
    ("B-ALP", "LCF", 4, "Jaguar"),
    ("B-BRV", "MRF", 6, "Lancer"),
    ("B-BRV", "DPS", 4, "Saber"),
    ("B-BRV", "LCF", 4, "Kite"),
    ("B-CHL", "LCF", 6, "Talon"),
    ("B-CHL", "DPS", 2, "Arrow"),
    ("B-DLT", "OMF", 4, "Hawk"),
    ("B-DLT", "TKR", 3, "Pelican"),
]

# (base id, weapon code, quantity)
STOCKS = [
    ("B-ALP", "SOW", 4),
    ("B-ALP", "LGB", 24),
    ("B-ALP", "GPS", 16),
    ("B-ALP", "ARM", 8),
    ("B-ALP", "AAM", 60),
    ("B-ALP", "UGB", 60),
    ("B-BRV", "SOW", 2),
    ("B-BRV", "LGB", 20),
    ("B-BRV", "GPS", 16),
    ("B-BRV", "ARM", 6),
    ("B-BRV", "AAM", 50),
    ("B-BRV", "UGB", 80),
    ("B-CHL", "LGB", 8),
    ("B-CHL", "GPS", 8),
    ("B-CHL", "AAM", 30),
    ("B-CHL", "UGB", 60),
    ("B-DLT", "SOW", 4),
    ("B-DLT", "LGB", 8),
    ("B-DLT", "GPS", 8),
    ("B-DLT", "ARM", 4),
    ("B-DLT", "AAM", 30),
]

SAMS = [
    SamSite(id="S-01", name="Long-range SAM 'Anvil'", position=_p(760, 420), range_km=110, lethality=0.8),
    SamSite(id="S-02", name="Medium SAM 'Spear North'", position=_p(640, 540), range_km=50, lethality=0.6),
    SamSite(id="S-03", name="Medium SAM 'Spear South'", position=_p(680, 230), range_km=50, lethality=0.6),
    SamSite(id="S-04", name="Medium SAM 'Spear East'", position=_p(860, 300), range_km=45, lethality=0.6),
    SamSite(
        id="S-05", name="Point-defence SAM 'Needle K'", position=_p(705, 515), range_km=20, lethality=0.5
    ),
    SamSite(
        id="S-06", name="Point-defence SAM 'Needle A'", position=_p(590, 300), range_km=15, lethality=0.4
    ),
    SamSite(id="S-07", name="Medium SAM 'Spear Coast'", position=_p(840, 140), range_km=45, lethality=0.6),
]

S, D, E = MissionType.STRIKE, MissionType.DCA, MissionType.SEAD
TC = TargetClass

MISSIONS = [
    # Defensive counter-air: two CAP stations, two shifts each.
    Mission(
        id="M-01",
        name="CAP North (shift 1)",
        type=D,
        target_class=TC.CAP_STATION,
        position=_p(430, 560),
        priority=85,
        window_start_min=45,
        window_end_min=75,
        aircraft_required=2,
        on_station_min=150,
    ),
    Mission(
        id="M-02",
        name="CAP South (shift 1)",
        type=D,
        target_class=TC.CAP_STATION,
        position=_p(440, 190),
        priority=80,
        window_start_min=45,
        window_end_min=75,
        aircraft_required=2,
        on_station_min=150,
    ),
    Mission(
        id="M-03",
        name="CAP North (shift 2)",
        type=D,
        target_class=TC.CAP_STATION,
        position=_p(430, 560),
        priority=75,
        window_start_min=480,
        window_end_min=510,
        aircraft_required=2,
        on_station_min=150,
    ),
    Mission(
        id="M-04",
        name="CAP South (shift 2)",
        type=D,
        target_class=TC.CAP_STATION,
        position=_p(440, 190),
        priority=70,
        window_start_min=480,
        window_end_min=510,
        aircraft_required=2,
        on_station_min=150,
    ),
    # SEAD ahead of the strike waves.
    Mission(
        id="M-05",
        name="SEAD Spear North",
        type=E,
        target_class=TC.SAM,
        position=_p(640, 540),
        priority=78,
        window_start_min=60,
        window_end_min=150,
        aircraft_required=2,
        sam_id="S-02",
    ),
    Mission(
        id="M-06",
        name="SEAD Spear South",
        type=E,
        target_class=TC.SAM,
        position=_p(680, 230),
        priority=72,
        window_start_min=200,
        window_end_min=300,
        aircraft_required=2,
        sam_id="S-03",
    ),
    Mission(
        id="M-07",
        name="SEAD Anvil",
        type=E,
        target_class=TC.SAM,
        position=_p(760, 420),
        priority=88,
        window_start_min=150,
        window_end_min=260,
        aircraft_required=4,
        sam_id="S-01",
    ),
    # Strike.
    Mission(
        id="M-08",
        name="Kestrel Airfield",
        type=S,
        target_class=TC.AIRFIELD,
        position=_p(700, 520),
        priority=90,
        window_start_min=90,
        window_end_min=210,
        aircraft_required=4,
    ),
    Mission(
        id="M-09",
        name="Ridge Radar Station",
        type=S,
        target_class=TC.RADAR,
        position=_p(600, 450),
        priority=80,
        window_start_min=60,
        window_end_min=180,
        aircraft_required=2,
    ),
    Mission(
        id="M-10",
        name="North River Bridge",
        type=S,
        target_class=TC.BRIDGE,
        position=_p(640, 610),
        priority=70,
        window_start_min=120,
        window_end_min=300,
        aircraft_required=2,
    ),
    Mission(
        id="M-11",
        name="Eastern Fuel Depot",
        type=S,
        target_class=TC.DEPOT,
        position=_p(820, 360),
        priority=75,
        window_start_min=180,
        window_end_min=360,
        aircraft_required=2,
    ),
    Mission(
        id="M-12",
        name="Corps Headquarters",
        type=S,
        target_class=TC.COMMAND,
        position=_p(760, 240),
        priority=95,
        window_start_min=240,
        window_end_min=420,
        aircraft_required=4,
    ),
    Mission(
        id="M-13",
        name="Armour Concentration",
        type=S,
        target_class=TC.ARMOUR,
        position=_p(590, 300),
        priority=65,
        window_start_min=300,
        window_end_min=480,
        aircraft_required=2,
    ),
    Mission(
        id="M-14",
        name="South Rail Bridge",
        type=S,
        target_class=TC.BRIDGE,
        position=_p(650, 130),
        priority=60,
        window_start_min=360,
        window_end_min=540,
        aircraft_required=2,
    ),
    Mission(
        id="M-15",
        name="Main Ammunition Depot",
        type=S,
        target_class=TC.DEPOT,
        position=_p(880, 500),
        priority=85,
        window_start_min=420,
        window_end_min=600,
        aircraft_required=2,
    ),
    Mission(
        id="M-16",
        name="Falcon Airfield",
        type=S,
        target_class=TC.AIRFIELD,
        position=_p(850, 150),
        priority=80,
        window_start_min=480,
        window_end_min=660,
        aircraft_required=4,
    ),
    Mission(
        id="M-17",
        name="Signals Node",
        type=S,
        target_class=TC.COMMAND,
        position=_p(700, 380),
        priority=70,
        window_start_min=540,
        window_end_min=720,
        aircraft_required=2,
    ),
    Mission(
        id="M-18",
        name="Logistics Hub",
        type=S,
        target_class=TC.DEPOT,
        position=_p(620, 200),
        priority=55,
        window_start_min=600,
        window_end_min=780,
        aircraft_required=2,
    ),
    Mission(
        id="M-19",
        name="Coastal Radar",
        type=S,
        target_class=TC.RADAR,
        position=_p(920, 80),
        priority=50,
        window_start_min=660,
        window_end_min=840,
        aircraft_required=2,
    ),
    Mission(
        id="M-20",
        name="Central Bridge",
        type=S,
        target_class=TC.BRIDGE,
        position=_p(720, 460),
        priority=60,
        window_start_min=720,
        window_end_min=880,
        aircraft_required=2,
    ),
    Mission(
        id="M-21",
        name="Forward Supply Point",
        type=S,
        target_class=TC.DEPOT,
        position=_p(580, 560),
        priority=45,
        window_start_min=780,
        window_end_min=900,
        aircraft_required=2,
    ),
    Mission(
        id="M-22",
        name="Radar Picket West",
        type=S,
        target_class=TC.RADAR,
        position=_p(560, 380),
        priority=58,
        window_start_min=150,
        window_end_min=330,
        aircraft_required=2,
    ),
    Mission(
        id="M-23",
        name="Pontoon Bridge",
        type=S,
        target_class=TC.BRIDGE,
        position=_p(600, 650),
        priority=52,
        window_start_min=240,
        window_end_min=420,
        aircraft_required=2,
    ),
    Mission(
        id="M-24",
        name="Artillery Park",
        type=S,
        target_class=TC.ARMOUR,
        position=_p(575, 470),
        priority=62,
        window_start_min=420,
        window_end_min=600,
        aircraft_required=4,
    ),
    Mission(
        id="M-25",
        name="Rail Marshalling Yard",
        type=S,
        target_class=TC.DEPOT,
        position=_p(780, 560),
        priority=66,
        window_start_min=600,
        window_end_min=780,
        aircraft_required=4,
    ),
    Mission(
        id="M-26",
        name="Air Defence Sector HQ",
        type=S,
        target_class=TC.COMMAND,
        position=_p(800, 430),
        priority=82,
        window_start_min=300,
        window_end_min=480,
        aircraft_required=4,
    ),
    Mission(
        id="M-27",
        name="Helicopter Forward Base",
        type=S,
        target_class=TC.AIRFIELD,
        position=_p(620, 260),
        priority=57,
        window_start_min=840,
        window_end_min=940,
        aircraft_required=2,
    ),
    Mission(
        id="M-28",
        name="Fuel Convoy Laager",
        type=S,
        target_class=TC.ARMOUR,
        position=_p(680, 300),
        priority=48,
        window_start_min=100,
        window_end_min=260,
        aircraft_required=2,
    ),
]

WEATHER = [
    WeatherCell(id="W-01", centre=_p(860, 470), radius_km=80, condition=WeatherCondition.CLOUD),
    WeatherCell(id="W-02", centre=_p(450, 650), radius_km=35, condition=WeatherCondition.STORM),
]

AIRSPACE = [
    AirspaceZone(id="A-01", name="Metro civil terminal area", centre=_p(250, 440), radius_km=40),
    AirspaceZone(id="A-02", name="R-21 weapons range", centre=_p(330, 80), radius_km=35),
]

SOURCE_SYSTEMS = {
    SourceSystem.MAINTENANCE: "Maintenance management system",
    SourceSystem.CREW: "Crew rostering system",
    SourceSystem.ARMAMENT: "Armament inventory",
    SourceSystem.AIRSPACE: "Airspace management cell",
    SourceSystem.MET: "Met office",
    SourceSystem.INTEL: "Intelligence fusion cell",
    SourceSystem.OPS: "Ops planning cell (task list)",
}


def build_scenario(seed: int) -> Cop:
    rng = random.Random(seed)
    aircraft: list[Aircraft] = []
    crews: list[Crew] = []
    for base_id, type_code, count, callsign in FLEET:
        base_letter = base_id[2]
        for n in range(1, count + 1):
            unserviceable = (base_id, type_code, n) in INITIAL_UNSERVICEABLE
            aircraft.append(
                Aircraft(
                    tail=f"{type_code}-{base_letter}{n:02d}",
                    type_code=type_code,
                    base_id=base_id,
                    status=AircraftStatus.UNSERVICEABLE if unserviceable else AircraftStatus.SERVICEABLE,
                    status_note="Awaiting spares (carried over)" if unserviceable else "",
                    hours_to_maintenance=round(rng.uniform(*HOURS_TO_MAINTENANCE_RANGE), 1),
                    snags_30d=rng.randint(0, MAX_SNAGS_30D),
                )
            )
        if type_code == "TKR":
            continue
        for n in range(1, math.ceil(count * CREWS_PER_AIRCRAFT) + 1):
            crews.append(
                Crew(
                    id=f"C-{type_code}-{base_letter}{n:02d}",
                    callsign=f"{callsign} {n}",
                    type_code=type_code,
                    base_id=base_id,
                    night_qualified=rng.random() < NIGHT_QUALIFIED_FRACTION,
                )
            )

    stocks = [WeaponStock(base_id=b, weapon_code=w, quantity=q) for b, w, q in STOCKS]
    record_counts = {
        SourceSystem.MAINTENANCE: len(aircraft),
        SourceSystem.CREW: len(crews),
        SourceSystem.ARMAMENT: len(stocks),
        SourceSystem.AIRSPACE: len(AIRSPACE),
        SourceSystem.MET: len(WEATHER),
        SourceSystem.INTEL: len(SAMS),
        SourceSystem.OPS: len(MISSIONS),
    }
    sources = [
        SourceStatus(source=s, system_name=name, last_update_min=0, records=record_counts[s])
        for s, name in SOURCE_SYSTEMS.items()
    ]
    feed = [
        FeedEntry(at_min=0, source=s, summary=f"Initial load: {record_counts[s]} records from {name}")
        for s, name in SOURCE_SYSTEMS.items()
    ]
    return Cop(
        scenario_name=SCENARIO_NAME,
        seed=seed,
        border=BORDER,
        aircraft_types=AIRCRAFT_TYPES,
        weapons=WEAPONS,
        bases=[b.model_copy(deep=True) for b in BASES],
        aircraft=aircraft,
        crews=crews,
        stocks=stocks,
        missions=[m.model_copy(deep=True) for m in MISSIONS],
        sams=[s.model_copy(deep=True) for s in SAMS],
        weather=[w.model_copy(deep=True) for w in WEATHER],
        airspace=[a.model_copy(deep=True) for a in AIRSPACE],
        sources=sources,
        feed=feed,
    )
