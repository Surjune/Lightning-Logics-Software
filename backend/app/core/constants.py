"""Every tunable number in the system, with its source or reasoning.

The scenario is fictional ("Blueland" vs "Redland"). Platform and weapon figures are
rounded, representative values for each class of system as described in open-source
literature. They are not the specification of any real system.
"""

from typing import Final

# --- Theatre geometry --------------------------------------------------------------
THEATRE_WIDTH_KM: Final = 1000.0
THEATRE_HEIGHT_KM: Final = 700.0
# 10 km cells keep the routing grid at 100 x 70 cells: fine enough to thread between
# SAM rings, coarse enough that one Dijkstra sweep per base stays well under 100 ms.
ROUTING_CELL_KM: Final = 10.0
# Sampling step when integrating threat exposure along a route. Smaller than the
# smallest SAM radius in the scenario (15 km) so no ring can be skipped between samples.
ROUTE_SAMPLE_KM: Final = 2.0
# How strongly the router detours around SAM coverage. A cell fully inside one
# lethality-1.0 ring costs (1 + factor) times its length, so 4.0 accepts roughly a
# 5x longer path to avoid a ring entirely.
THREAT_AVOIDANCE_FACTOR: Final = 4.0

# --- Planning clock ----------------------------------------------------------------
# The flying day starts at 05:00 local; all scenario times are minutes after that.
DAY_START_CLOCK_MIN: Final = 5 * 60
HORIZON_MIN: Final = 16 * 60  # 05:00 to 21:00
NIGHT_END_MIN: Final = 60  # 06:00 local (first light)
NIGHT_START_MIN: Final = 13 * 60 + 30  # 18:30 local (last light)
# Packages launching within this window are committed: crews briefed, weapons loaded.
# Retasking does not touch them unless an asset in them becomes invalid.
ARMING_LEAD_TIME_MIN: Final = 30
# Time from engine start to setting course (taxi, take-off, join-up).
FORM_UP_MIN: Final = 15
STRIKE_TIME_ON_TARGET_MIN: Final = 10
SEAD_TIME_ON_TARGET_MIN: Final = 15

# --- Aircraft reliability heuristic ------------------------------------------------
# Transparent placeholder for a model trained on real maintenance records: the
# interface (aircraft -> probability) stays fixed when a trained model replaces it.
BASE_SERVICEABILITY: Final = 0.97
SERVICEABILITY_PENALTY_PER_SNAG: Final = 0.02  # per defect raised in the last 30 days
SERVICEABILITY_PENALTY_PER_SORTIE_TODAY: Final = 0.03  # wear from today's flying
LOW_HOURS_THRESHOLD: Final = 5.0  # hours to next scheduled maintenance
LOW_HOURS_PENALTY: Final = 0.10
MIN_SERVICEABILITY: Final = 0.50
MAX_SERVICEABILITY: Final = 0.99
# Aircraft predicted below this are not offered to the planner at all.
SERVICEABILITY_PLANNING_FLOOR: Final = 0.70

# --- Crew limits -------------------------------------------------------------------
# Flight-duty limits are representative of military fast-jet practice: 8 h flying
# duty and 2 combat sorties per crew per day, 60 min rest between sorties.
CREW_MAX_DUTY_MIN: Final = 8 * 60
CREW_MAX_SORTIES_PER_DAY: Final = 2
CREW_MIN_REST_MIN: Final = 60
# Fatigue score: fraction of duty used, plus a penalty for flying in the circadian low.
NIGHT_FATIGUE_PENALTY: Final = 0.15
CREW_FATIGUE_LIMIT: Final = 0.90

# --- Risk model --------------------------------------------------------------------
# Probability of loss per km flown inside a lethality-1.0 SAM envelope by an aircraft
# with zero survivability. 0.004/km gives ~18% loss for a 50 km unescorted penetration
# of a high-lethality ring at survivability 0, ~7% at survivability 0.6.
RISK_PER_EXPOSURE_KM: Final = 0.004
# Range extension when an air-to-air refueller supports the sortie.
AAR_RADIUS_FACTOR: Final = 1.4
TANKER_RECEIVERS_PER_SORTIE: Final = 4
TANKER_SORTIES_PER_DAY: Final = 2

# --- Optimiser ---------------------------------------------------------------------
# Objective terms are expressed in "priority points" (mission priority is 1-100) and
# scaled to integers for CP-SAT.
OBJECTIVE_SCALE: Final = 100
# Losing one aircraft and crew is valued at 60 priority points before COA weighting.
AIRCRAFT_LOSS_COST: Final = 60.0
# Changing one aircraft's tasking during retasking costs 6 priority points before COA
# weighting: enough to stop churn for marginal gains, small against any real mission.
CHANGE_COST: Final = 6.0
# Keeping the same crew on a retained package earns 2 points: crews in a squadron are
# interchangeable to the solver, and without this a retask reshuffles them for nothing.
CREW_CHANGE_COST: Final = 2.0
# Per-COA search budget in CP-SAT deterministic time units, so every run does the same
# amount of search whatever else the host is doing. 0.3 units is ~2 s on an idle 4-core
# laptop and reaches the same objective as an 8 s wall-clock run on the demo scenario.
# COAs run one after another: concurrent solves starve each other's worker threads.
SOLVER_DETERMINISTIC_BUDGET: Final = 0.3
# Wall-clock safety cap per COA for a heavily loaded host; past it the best plan found
# so far is used (or the greedy starting plan if none was reported yet).
SOLVER_WALL_CAP_S: Final = 4.0
# Presolve probing costs ~1.5 s on this model size for no measurable gain; level 1
# keeps the cheap probing passes only.
SOLVER_PROBING_LEVEL: Final = 1
# Pruning before solving, per COA: the best loadouts per (aircraft, mission) and the
# best candidates per mission. Six options per aircraft slot leaves the solver ample
# room to trade assets between missions while keeping the model small.
LOADOUTS_PER_PAIR: Final = 2
CANDIDATES_PER_SLOT: Final = 6
# Time-on-target step the greedy baseline tries within each mission window.
GREEDY_TOT_STEP_MIN: Final = 5
SOLVER_WORKERS: Final = 4
SOLVER_RANDOM_SEED: Final = 7
SCENARIO_SEED: Final = 26250

# COA weight profiles: (risk weight, scarce-munition weight, change weight).
COA_BALANCED: Final = (1.0, 1.0, 1.0)
COA_MAX_EFFECT: Final = (0.4, 0.3, 0.25)
COA_MIN_RISK: Final = (3.0, 1.0, 1.0)
# Retask re-plan: balanced, but each changed aircraft tasking costs twice as much, so
# assets are pulled from other packages only for a clear gain.
COA_BALANCED_REPLAN: Final = (1.0, 1.0, 2.0)

# --- Data fusion -------------------------------------------------------------------
# Two SAM reports within this distance are treated as the same site.
SAM_CORRELATION_KM: Final = 15.0
SINGLE_SOURCE_CONFIDENCE: Final = 0.8
# A source silent for longer than these is flagged amber / red in the picture.
SOURCE_STALE_AMBER_MIN: Final = 60
SOURCE_STALE_RED_MIN: Final = 120

# --- Auto-generated missions -------------------------------------------------------
POPUP_SAM_SEAD_PRIORITY: Final = 72
SEAD_WINDOW_DELAY_MIN: Final = 45
SEAD_WINDOW_LENGTH_MIN: Final = 120
SEAD_PACKAGE_SIZE: Final = 2
