"""Greedy priority-first planner.

Two jobs: it is the baseline the optimiser's plans are compared against (what a planner
patching the plan by hand, highest priority first, would produce), and its plan is the
optimiser's starting solution, so CP-SAT begins from a feasible plan and improves it.

It enforces exactly the constraints the optimiser enforces, including the conservative
per-(base, type) crew interval, so its output is always a valid solver hint.
"""

from collections import defaultdict
from dataclasses import dataclass, replace

from app.core.constants import CREW_MIN_REST_MIN, GREEDY_TOT_STEP_MIN
from app.domain.models import Mission
from app.engine.candidates import Candidate, CandidateSet, Commitments
from app.engine.optimizer import Assignment, CoaWeights, candidate_score


@dataclass(frozen=True)
class PreviousPackage:
    mission_id: str
    tot_min: int
    crew_by_tail: dict[str, str]
    weapon_by_tail: dict[str, str]


@dataclass(frozen=True)
class Repair:
    """Result of keeping every previous package that is still feasible as planned."""

    kept: list[Assignment]
    missions: list[Mission]  # missions still to plan
    cset: CandidateSet  # their candidates, with resource ledgers net of the kept packages
    commitments: Commitments  # busy intervals including the kept packages


def _overlaps(busy: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < e and s < end for s, e in busy)


class _Ledger:
    def __init__(self, cset: CandidateSet, commitments: Commitments) -> None:
        self.cset = cset
        self.aircraft_busy = defaultdict(list, {k: list(v) for k, v in commitments.aircraft_busy.items()})
        self.crew_busy = defaultdict(list, {k: list(v) for k, v in commitments.crew_busy.items()})
        self.aircraft_minutes = dict(cset.aircraft_minutes_left)
        self.crew_duty = dict(cset.crew_duty_left)
        self.crew_sorties = dict(cset.crew_sorties_left)
        self.stock = dict(cset.stock_left)
        self.tanker = cset.tanker_capacity

    def try_package(
        self, m: Mission, tot: int, ordered: list[Candidate], preferred_crew: dict[str, str]
    ) -> list[Assignment] | None:
        chosen: list[Assignment] = []
        tails: set[str] = set()
        crews: set[str] = set()
        stock_use: dict[tuple[str, str], int] = defaultdict(int)
        aar = 0
        for c in ordered:
            if c.tail in tails or not (c.earliest_tot <= tot <= c.latest_tot):
                continue
            start = tot - c.pre_min
            if _overlaps(self.aircraft_busy[c.tail], start, start + c.duration_min + c.turnaround_min):
                continue
            if c.duration_min > self.aircraft_minutes[c.tail]:
                continue
            key = (c.base_id, c.weapon_code)
            if self.stock.get(key, 0) - stock_use[key] < c.qty:
                continue
            if c.needs_aar and self.tanker - aar < 1:
                continue
            g_pre, g_dur = self.cset.crew_windows[(m.id, c.base_id, c.type_code)]
            g_start = tot - g_pre
            pool = self.cset.crews[m.id].get((c.base_id, c.type_code), [])
            ordered_pool = sorted(pool, key=lambda k: (k != preferred_crew.get(c.tail), k))
            crew = next(
                (
                    k
                    for k in ordered_pool
                    if k not in crews
                    and self.crew_sorties[k] > 0
                    and self.crew_duty[k] >= g_dur
                    and not _overlaps(self.crew_busy[k], g_start, g_start + g_dur + CREW_MIN_REST_MIN)
                ),
                None,
            )
            if crew is None:
                continue
            chosen.append(Assignment(candidate=c, crew_id=crew, tot_min=tot))
            tails.add(c.tail)
            crews.add(crew)
            stock_use[key] += c.qty
            aar += int(c.needs_aar)
            if len(chosen) == m.aircraft_required:
                return chosen
        return None

    def commit(self, m: Mission, package: list[Assignment]) -> None:
        for a in package:
            c = a.candidate
            start = a.tot_min - c.pre_min
            self.aircraft_busy[c.tail].append((start, start + c.duration_min + c.turnaround_min))
            self.aircraft_minutes[c.tail] -= c.duration_min
            self.stock[(c.base_id, c.weapon_code)] -= c.qty
            self.tanker -= int(c.needs_aar)
            g_pre, g_dur = self.cset.crew_windows[(m.id, c.base_id, c.type_code)]
            self.crew_busy[a.crew_id].append(
                (a.tot_min - g_pre, a.tot_min - g_pre + g_dur + CREW_MIN_REST_MIN)
            )
            self.crew_duty[a.crew_id] -= g_dur
            self.crew_sorties[a.crew_id] -= 1


def _keep_pass(
    ledger: _Ledger, missions: list[Mission], weights: CoaWeights, previous: list[PreviousPackage]
) -> list[Assignment]:
    """Keep each previous package that is still feasible with the same aircraft, crews,
    loadouts and time on target. Highest priority first, so conflicts favour it."""
    by_id = {m.id: m for m in missions}
    kept: list[Assignment] = []
    for prev in sorted(previous, key=lambda p: -by_id[p.mission_id].priority if p.mission_id in by_id else 0):
        m = by_id.get(prev.mission_id)
        if m is None:
            continue
        ordered = sorted(
            (c for c in ledger.cset.by_mission.get(m.id, []) if c.tail in prev.crew_by_tail),
            key=lambda c: (c.weapon_code != prev.weapon_by_tail.get(c.tail), -candidate_score(c, weights)),
        )
        package = ledger.try_package(m, prev.tot_min, ordered, prev.crew_by_tail)
        if package:
            ledger.commit(m, package)
            kept.extend(package)
    return kept


def keep_valid_packages(
    missions: list[Mission],
    cset: CandidateSet,
    commitments: Commitments,
    weights: CoaWeights,
    previous: list[PreviousPackage],
) -> Repair:
    ledger = _Ledger(cset, commitments)
    kept = _keep_pass(ledger, missions, weights, previous)
    done = {a.candidate.mission_id for a in kept}
    remaining = {mid: cands for mid, cands in cset.by_mission.items() if mid not in done}
    rest = replace(
        cset,
        candidates=[c for cands in remaining.values() for c in cands],
        by_mission=remaining,
        aircraft_minutes_left=dict(ledger.aircraft_minutes),
        crew_duty_left=dict(ledger.crew_duty),
        crew_sorties_left=dict(ledger.crew_sorties),
        stock_left=dict(ledger.stock),
        tanker_capacity=ledger.tanker,
    )
    busy = Commitments()
    for tail, intervals in ledger.aircraft_busy.items():
        busy.aircraft_busy[tail].extend(intervals)
    for crew, intervals in ledger.crew_busy.items():
        busy.crew_busy[crew].extend(intervals)
    return Repair(kept=kept, missions=[m for m in missions if m.id not in done], cset=rest, commitments=busy)


def greedy_plan(
    missions: list[Mission],
    cset: CandidateSet,
    commitments: Commitments,
    weights: CoaWeights,
    previous: list[PreviousPackage],
) -> list[Assignment]:
    ledger = _Ledger(cset, commitments)
    out = _keep_pass(ledger, missions, weights, previous)
    done = {a.candidate.mission_id for a in out}

    # Then fill the remaining missions in priority order at the earliest feasible time.
    for m in sorted(missions, key=lambda m: -m.priority):
        if m.id in done or not cset.by_mission.get(m.id):
            continue
        ordered = sorted(cset.by_mission[m.id], key=lambda c: -candidate_score(c, weights))
        times = list(range(m.window_start_min, m.window_end_min + 1, GREEDY_TOT_STEP_MIN))
        if times[-1] != m.window_end_min:
            times.append(m.window_end_min)
        for tot in times:
            package = ledger.try_package(m, tot, ordered, {})
            if package:
                ledger.commit(m, package)
                out.extend(package)
                break
    return out
