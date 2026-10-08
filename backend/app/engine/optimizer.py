"""CP-SAT allocation of aircraft, crews and weapons to missions.

Decision variables
  x[c]    candidate c (aircraft + loadout for a mission) flies
  y[m]    mission m is resourced (its whole package flies)
  tot[m]  time on target for mission m (shared by its package)
  z[k,m]  crew k flies on mission m

Hard constraints: package size, aircraft and crew time overlap (with turnaround and
rest), crew sorties and duty, aircraft hours to maintenance, weapon stock per base,
tanker capacity, and each candidate's own feasible time-on-target range.

Objective (priority points, scaled to integers):
  sum x[c] * (value - w_risk * risk * loss_cost - w_scarce * scarcity)
  - w_change * change_cost * (number of aircraft whose tasking changes vs the old plan)

Retasking passes the previous plan's (aircraft, mission) pairs: keeping them earns the
change cost back, so the solver only reshuffles when the gain outweighs the disruption.
The previous plan is also given as a solution hint, so the search starts from it.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field, replace

from ortools.sat.python import cp_model

from app.core.constants import (
    AIRCRAFT_LOSS_COST,
    CANDIDATES_PER_SLOT,
    CHANGE_COST,
    CREW_CHANGE_COST,
    CREW_MIN_REST_MIN,
    LOADOUTS_PER_PAIR,
    OBJECTIVE_SCALE,
    SOLVER_PROBING_LEVEL,
    SOLVER_RANDOM_SEED,
    SOLVER_WALL_CAP_S,
    SOLVER_WORKERS,
)
from app.core.exceptions import SolverError
from app.core.logging import get_logger, log_fields
from app.domain.models import Mission
from app.engine.candidates import Candidate, CandidateSet, Commitments

logger = get_logger(__name__)


@dataclass(frozen=True)
class CoaWeights:
    risk: float
    scarce: float
    change: float


@dataclass(frozen=True)
class Assignment:
    candidate: Candidate
    crew_id: str
    tot_min: int


@dataclass(frozen=True)
class SolveResult:
    assignments: list[Assignment]
    status: str
    solve_ms: int
    objective: float


@dataclass(frozen=True)
class PreviousPlan:
    """The open part of the plan being retasked. Empty when planning from scratch."""

    pairs: frozenset[tuple[str, str]] = frozenset()  # (aircraft tail, mission id)
    crews: dict[tuple[str, str], str] = field(default_factory=dict)  # (mission, tail) -> crew

    @property
    def crew_pairs(self) -> set[tuple[str, str]]:
        return {(crew, mission) for (mission, _), crew in self.crews.items()}


def candidate_score(c: Candidate, w: CoaWeights) -> float:
    """Net priority points for flying one candidate, before plan-stability terms."""
    return c.value - w.risk * c.risk * AIRCRAFT_LOSS_COST - w.scarce * c.scarcity_cost


def plan_objective(assignments: list[Assignment], weights: CoaWeights, previous: PreviousPlan) -> float:
    """The solver's objective evaluated for a given plan, in priority points."""
    total = 0.0
    crew_pairs = previous.crew_pairs
    for a in assignments:
        c = a.candidate
        total += candidate_score(c, weights)
        if previous.pairs:
            keep = (c.tail, c.mission_id) in previous.pairs
            total += weights.change * CHANGE_COST * (1 if keep else -1)
        if (a.crew_id, c.mission_id) in crew_pairs:
            total += weights.change * CREW_CHANGE_COST
    return total


def prune(
    cset: CandidateSet, missions: list[Mission], weights: CoaWeights, keep_pairs: set[tuple[str, str]]
) -> CandidateSet:
    """Keep the best options per mission under this COA's weights.

    Options for (aircraft, mission) pairs in the current plan are always kept so the
    retasker can leave a package exactly as it was.
    """
    required = {m.id: m.aircraft_required for m in missions}
    by_mission: dict[str, list[Candidate]] = {}
    for mid, cands in cset.by_mission.items():
        ranked = sorted(cands, key=lambda c: -candidate_score(c, weights))
        per_pair: dict[str, int] = defaultdict(int)
        kept: list[Candidate] = []
        limit = required.get(mid, 1) * CANDIDATES_PER_SLOT
        for c in ranked:
            pinned = (c.tail, mid) in keep_pairs
            if per_pair[c.tail] >= LOADOUTS_PER_PAIR and not pinned:
                continue
            if len(kept) >= limit and not pinned:
                continue
            kept.append(c)
            per_pair[c.tail] += 1
        by_mission[mid] = kept
    return replace(cset, candidates=[c for cs in by_mission.values() for c in cs], by_mission=by_mission)


def solve(
    missions: list[Mission],
    cset: CandidateSet,
    commitments: Commitments,
    weights: CoaWeights,
    previous: PreviousPlan,
    hint: list[Assignment],
    deterministic_budget: float,
) -> SolveResult:
    model = cp_model.CpModel()
    started = time.perf_counter()

    x = {c.idx: model.new_bool_var(f"x{c.idx}") for c in cset.candidates}
    y: dict[str, cp_model.IntVar] = {}
    tot: dict[str, cp_model.IntVar] = {}
    aircraft_intervals: dict[str, list[cp_model.IntervalVar]] = defaultdict(list)
    crew_intervals: dict[str, list[cp_model.IntervalVar]] = defaultdict(list)
    crew_load: dict[str, list[tuple[cp_model.IntVar, int]]] = defaultdict(list)
    z: dict[tuple[str, str], cp_model.IntVar] = {}

    for m in missions:
        cands = cset.by_mission.get(m.id, [])
        if not cands:
            continue
        y[m.id] = model.new_bool_var(f"y_{m.id}")
        tot[m.id] = model.new_int_var(m.window_start_min, m.window_end_min, f"tot_{m.id}")
        model.add(sum(x[c.idx] for c in cands) == m.aircraft_required * y[m.id])

        groups: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
        for c in cands:
            model.add(tot[m.id] >= c.earliest_tot).only_enforce_if(x[c.idx])
            model.add(tot[m.id] <= c.latest_tot).only_enforce_if(x[c.idx])
            start = tot[m.id] - c.pre_min
            size = c.duration_min + c.turnaround_min
            aircraft_intervals[c.tail].append(
                model.new_optional_interval_var(start, size, start + size, x[c.idx], f"ia{c.idx}")
            )
            groups[(c.base_id, c.type_code)].append(c)

        # Crews: per (base, type) group, crews assigned must equal aircraft flying.
        for (base_id, type_code), group in groups.items():
            crew_ids = cset.crews[m.id].get((base_id, type_code), [])
            pre, duration = cset.crew_windows[(m.id, base_id, type_code)]
            zs = []
            for k in crew_ids:
                if cset.crew_duty_left[k] < duration:
                    continue
                var = model.new_bool_var(f"z_{k}_{m.id}")
                z[(k, m.id)] = var
                zs.append(var)
                start = tot[m.id] - pre
                size = duration + CREW_MIN_REST_MIN
                crew_intervals[k].append(model.new_optional_interval_var(start, size, start + size, var, ""))
                crew_load[k].append((var, duration))
            model.add(sum(zs) == sum(x[c.idx] for c in group))

    for tail, intervals in aircraft_intervals.items():
        intervals += [
            model.new_fixed_size_interval_var(s, e - s, "") for s, e in commitments.aircraft_busy[tail]
        ]
        model.add_no_overlap(intervals)
    for k, intervals in crew_intervals.items():
        intervals += [model.new_fixed_size_interval_var(s, e - s, "") for s, e in commitments.crew_busy[k]]
        model.add_no_overlap(intervals)
        model.add(sum(v for v, _ in crew_load[k]) <= cset.crew_sorties_left[k])
        model.add(sum(v * d for v, d in crew_load[k]) <= cset.crew_duty_left[k])

    by_tail: dict[str, list[Candidate]] = defaultdict(list)
    by_stock: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for c in cset.candidates:
        by_tail[c.tail].append(c)
        by_stock[(c.base_id, c.weapon_code)].append(c)
    for tail, cands in by_tail.items():
        model.add(sum(x[c.idx] * c.duration_min for c in cands) <= cset.aircraft_minutes_left[tail])
    for key, cands in by_stock.items():
        model.add(sum(x[c.idx] * c.qty for c in cands) <= cset.stock_left.get(key, 0))
    model.add(sum(x[c.idx] for c in cset.candidates if c.needs_aar) <= cset.tanker_capacity)

    terms = []
    for c in cset.candidates:
        keep = (c.tail, c.mission_id) in previous.pairs
        stability = weights.change * CHANGE_COST * (1 if keep else -1) if previous.pairs else 0.0
        terms.append(x[c.idx] * round(OBJECTIVE_SCALE * (candidate_score(c, weights) + stability)))
    crew_bonus = round(OBJECTIVE_SCALE * weights.change * CREW_CHANGE_COST)
    terms += [var * crew_bonus for key, var in z.items() if key in previous.crew_pairs]
    model.maximize(sum(terms))

    # Start the search from the greedy plan: a complete, feasible solution.
    hinted = {a.candidate.idx for a in hint}
    hinted_crews = {(a.crew_id, a.candidate.mission_id) for a in hint}
    hinted_tot = {a.candidate.mission_id: a.tot_min for a in hint}
    for idx, var in x.items():
        model.add_hint(var, 1 if idx in hinted else 0)
    window_start = {m.id: m.window_start_min for m in missions}
    for mid, var in y.items():
        model.add_hint(var, 1 if mid in hinted_tot else 0)
        # Unresourced missions still need a time-on-target value for the hint to be
        # complete; any value in the window is consistent when no aircraft flies it.
        model.add_hint(tot[mid], hinted_tot.get(mid, window_start[mid]))
    for key, var in z.items():
        model.add_hint(var, 1 if key in hinted_crews else 0)

    solver = cp_model.CpSolver()
    solver.parameters.max_deterministic_time = deterministic_budget
    solver.parameters.max_time_in_seconds = SOLVER_WALL_CAP_S
    solver.parameters.num_workers = SOLVER_WORKERS
    solver.parameters.random_seed = SOLVER_RANDOM_SEED
    solver.parameters.cp_model_probing_level = SOLVER_PROBING_LEVEL
    status = solver.solve(model)
    status_name = solver.status_name(status)
    if status == cp_model.UNKNOWN:
        # Out of time before any solution was reported (e.g. a heavily loaded host):
        # the starting plan is feasible by construction, so fall back to it.
        solve_ms = round((time.perf_counter() - started) * 1000)
        logger.warning("solver timed out, using starting plan", extra=log_fields(solve_ms=solve_ms))
        return SolveResult(
            assignments=hint,
            status="FALLBACK_GREEDY",
            solve_ms=solve_ms,
            objective=plan_objective(hint, weights, previous),
        )
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SolverError(f"Optimiser returned {status_name}")

    assignments: list[Assignment] = []
    for m in missions:
        if m.id not in y or not solver.value(y[m.id]):
            continue
        t = solver.value(tot[m.id])
        chosen = sorted((c for c in cset.by_mission[m.id] if solver.value(x[c.idx])), key=lambda c: c.tail)
        free_crews: dict[tuple[str, str], list[str]] = defaultdict(list)
        for (k, mid), var in z.items():
            if mid == m.id and solver.value(var):
                crew_key = next(key for key, ids in cset.crews[m.id].items() if k in ids)
                free_crews[crew_key].append(k)
        # Pair each aircraft with its previous crew where that crew is still selected.
        crew_for: dict[str, str] = {}
        for c in chosen:
            preferred = previous.crews.get((m.id, c.tail))
            pool = free_crews[(c.base_id, c.type_code)]
            if preferred in pool:
                crew_for[c.tail] = preferred
                pool.remove(preferred)
        for c in chosen:
            if c.tail not in crew_for:
                pool = sorted(free_crews[(c.base_id, c.type_code)])
                crew_for[c.tail] = pool[0]
                free_crews[(c.base_id, c.type_code)].remove(pool[0])
            assignments.append(Assignment(candidate=c, crew_id=crew_for[c.tail], tot_min=t))

    solve_ms = round((time.perf_counter() - started) * 1000)
    logger.info(
        "solve complete",
        extra=log_fields(
            status=status_name,
            candidates=len(cset.candidates),
            solve_ms=solve_ms,
            assigned=len(assignments),
            weights=weights.__dict__,
        ),
    )
    return SolveResult(
        assignments=assignments,
        status=status_name,
        solve_ms=solve_ms,
        objective=solver.objective_value / OBJECTIVE_SCALE,
    )
