from app.core.constants import COA_BALANCED
from app.domain.enums import AircraftStatus
from app.domain.models import Cop
from app.engine.assemble import to_packages
from app.engine.baseline import greedy_plan
from app.engine.candidates import Commitments, generate_candidates
from app.engine.optimizer import (
    Assignment,
    CoaWeights,
    PreviousPlan,
    SolveResult,
    plan_objective,
    prune,
    solve,
)
from app.engine.routing import Router
from app.engine.validator import validate

BUDGET = 0.1
W = CoaWeights(*COA_BALANCED)


def _solve(cop: Cop) -> tuple[list[Assignment], SolveResult]:
    full = generate_candidates(cop, Router.from_cop(cop), cop.missions, cop.clock_min, Commitments())
    cset = prune(full, cop.missions, W, set())
    hint = greedy_plan(cop.missions, cset, Commitments(), W, [])
    return hint, solve(cop.missions, cset, Commitments(), W, PreviousPlan(), hint, BUDGET)


def test_plan_satisfies_every_hard_constraint(cop: Cop) -> None:
    _, result = _solve(cop)
    assert result.assignments
    assert validate(cop, to_packages(result.assignments)) == []


def test_optimiser_never_worse_than_its_starting_plan(cop: Cop) -> None:
    hint, result = _solve(cop)
    assert plan_objective(result.assignments, W, PreviousPlan()) >= plan_objective(hint, W, PreviousPlan())


def test_packages_are_complete_and_share_time_on_target(cop: Cop) -> None:
    _, result = _solve(cop)
    for p in to_packages(result.assignments):
        assert len(p.sorties) == cop.mission(p.mission_id).aircraft_required
        assert {s.tot_min for s in p.sorties} == {p.tot_min}


def test_higher_priority_wins_when_only_one_package_fits(cop: Cop) -> None:
    # Two identical missions at the same time; exactly two aircraft are able to fly.
    keep = {"OMF-A01", "OMF-A02"}
    for a in cop.aircraft:
        if a.tail not in keep:
            a.status = AircraftStatus.UNSERVICEABLE
    hi = cop.mission("M-09").model_copy(update={"priority": 90})
    lo = cop.mission("M-09").model_copy(update={"id": "M-90", "priority": 40})
    cop.missions = [hi, lo]
    _, result = _solve(cop)
    assert {a.candidate.mission_id for a in result.assignments} == {"M-09"}
