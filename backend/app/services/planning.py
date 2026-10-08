"""Plan generation, dynamic retasking and commander approval."""

import uuid
from dataclasses import dataclass

from app.core.constants import (
    AIRCRAFT_LOSS_COST,
    ARMING_LEAD_TIME_MIN,
    CHANGE_COST,
    COA_BALANCED,
    COA_MAX_EFFECT,
    COA_MIN_RISK,
    SOLVER_TIME_LIMIT_S,
)
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger, log_fields
from app.domain.enums import AircraftStatus, PackageStatus
from app.domain.models import Cop, Mission
from app.domain.plan import Coa, MissionExplanation, Package, Plan, PlanKpis, Proposal, UnassignedMission
from app.engine import prediction
from app.engine.assemble import to_packages
from app.engine.baseline import PreviousPackage, greedy_plan
from app.engine.candidates import CandidateSet, Commitments, commitments_from, generate_candidates
from app.engine.explain import diff_plans, explain_mission, tasking_changes, unassigned
from app.engine.kpis import compute_kpis
from app.engine.optimizer import Assignment, CoaWeights, SolveResult, plan_objective, prune, solve
from app.engine.validator import validate
from app.services.state import OpsState

logger = get_logger(__name__)


@dataclass(frozen=True)
class CoaProfile:
    id: str
    name: str
    rationale: str
    weights: CoaWeights


PROFILES = [
    CoaProfile(
        "A",
        "Balanced",
        "Commander's default weighting of effect, risk, munitions and plan stability.",
        CoaWeights(*COA_BALANCED),
    ),
    CoaProfile(
        "B",
        "Maximum effect",
        "Accepts more risk, spends scarce munitions and churns the plan "
        "to cover as much priority as possible.",
        CoaWeights(*COA_MAX_EFFECT),
    ),
    CoaProfile(
        "C",
        "Minimum risk",
        "Avoids SAM exposure, preferring standoff weapons and dropping tasks that cannot be flown safely.",
        CoaWeights(*COA_MIN_RISK),
    ),
]


def _package_still_valid(cop: Cop, p: Package) -> bool:
    aircraft = {a.tail: a for a in cop.aircraft}
    crews = {c.id: c for c in cop.crews}
    for s in p.sorties:
        if aircraft[s.aircraft_tail].status != AircraftStatus.SERVICEABLE or not crews[s.crew_id].available:
            return False
        if prediction.base_unavailable_reason(cop.base(s.base_id), cop.weather):
            return False
    return True


def split_plan(cop: Cop, packages: list[Package]) -> tuple[list[Package], list[Package]]:
    """(frozen, open): airborne, flown and valid committed packages are frozen."""
    frozen: list[Package] = []
    open_: list[Package] = []
    for p in packages:
        if p.status in (PackageStatus.AIRBORNE, PackageStatus.COMPLETE):
            frozen.append(p)
        elif min(
            s.launch_min for s in p.sorties
        ) <= cop.clock_min + ARMING_LEAD_TIME_MIN and _package_still_valid(cop, p):
            frozen.append(p.model_copy(update={"status": PackageStatus.COMMITTED}))
        else:
            open_.append(p.model_copy(update={"status": PackageStatus.PLANNED}))
    return frozen, open_


@dataclass(frozen=True)
class _Context:
    cop: Cop
    open_missions: list[Mission]
    frozen: list[Package]
    open_previous: list[Package]
    commitments: Commitments
    full: CandidateSet
    previous_pairs: set[tuple[str, str]]
    previous: list[PreviousPackage]


class PlanningService:
    def __init__(self, state: OpsState) -> None:
        self.state = state

    def _context(self) -> _Context:
        cop = self.state.cop
        frozen, open_previous = split_plan(cop, self.state.plan.packages)
        frozen_ids = {p.mission_id for p in frozen}
        open_missions = [m for m in cop.missions if m.id not in frozen_ids]
        commitments = commitments_from(cop, frozen)
        full = generate_candidates(
            cop, self.state.routers.get(cop), open_missions, cop.clock_min, commitments
        )
        return _Context(
            cop=cop,
            open_missions=open_missions,
            frozen=frozen,
            open_previous=open_previous,
            commitments=commitments,
            full=full,
            previous_pairs={(s.aircraft_tail, p.mission_id) for p in open_previous for s in p.sorties},
            previous=[
                PreviousPackage(p.mission_id, p.tot_min, {s.aircraft_tail: s.crew_id for s in p.sorties})
                for p in open_previous
            ],
        )

    def _search(self, ctx: _Context, profile: CoaProfile) -> SolveResult:
        cset = prune(ctx.full, ctx.open_missions, profile.weights, ctx.previous_pairs)
        hint = greedy_plan(ctx.open_missions, cset, ctx.commitments, profile.weights, ctx.previous)
        return solve(
            ctx.open_missions,
            cset,
            ctx.commitments,
            profile.weights,
            ctx.previous_pairs,
            hint,
            SOLVER_TIME_LIMIT_S,
        )

    def _build_coa(
        self, ctx: _Context, profile: CoaProfile, plan: list[Assignment], result: SolveResult
    ) -> Coa:
        new_packages = to_packages(plan)
        packages = sorted(ctx.frozen + new_packages, key=lambda p: (p.tot_min, p.mission_id))
        violations = validate(ctx.cop, packages)
        changes = tasking_changes(ctx.open_previous, new_packages) if self.state.plan.packages else 0
        return Coa(
            id=profile.id,
            name=profile.name,
            rationale=profile.rationale,
            packages=packages,
            unassigned=unassigned(ctx.cop, packages, ctx.full),
            kpis=compute_kpis(ctx.cop, packages, changes, len(violations)),
            changes=diff_plans(ctx.cop, self.state.plan.packages, packages, ctx.full),
            violations=violations,
            solver_status=result.status,
            solve_ms=result.solve_ms,
        )

    @staticmethod
    def _utility(kpis: PlanKpis, cop: Cop) -> float:
        """Balanced utility in priority points, used to recommend one COA."""
        total_priority = sum(m.priority for m in cop.missions)
        effect_points = kpis.expected_effect_pct / 100 * total_priority
        return effect_points - AIRCRAFT_LOSS_COST * kpis.expected_losses - CHANGE_COST * kpis.changes

    def generate(self, trigger: str) -> Proposal:
        with self.state.lock:
            ctx = self._context()
            results = [self._search(ctx, profile) for profile in PROFILES]
            # Every search result satisfies the same hard constraints, so each COA keeps
            # whichever result scores best under its own weights. A COA can then never
            # be beaten on its own objective by another COA's plan.
            coas = []
            for profile, own in zip(PROFILES, results, strict=True):
                best = max(
                    results, key=lambda r: plan_objective(r.assignments, profile.weights, ctx.previous_pairs)
                )
                coas.append(self._build_coa(ctx, profile, best.assignments, own))
            balanced = PROFILES[0].weights
            baseline_packages = ctx.frozen + to_packages(
                greedy_plan(
                    ctx.open_missions,
                    prune(ctx.full, ctx.open_missions, balanced, ctx.previous_pairs),
                    ctx.commitments,
                    balanced,
                    ctx.previous,
                )
            )
            baseline_kpis = compute_kpis(
                ctx.cop, baseline_packages, 0, len(validate(ctx.cop, baseline_packages))
            )
            recommended = max(coas, key=lambda c: (c.kpis.violations == 0, self._utility(c.kpis, ctx.cop)))
            proposal = Proposal(
                id=uuid.uuid4().hex[:8],
                trigger=trigger,
                created_min=ctx.cop.clock_min,
                coas=coas,
                baseline_kpis=baseline_kpis,
                recommended_coa_id=recommended.id,
            )
            self.state.proposal = proposal
            self.state.last_candidates = ctx.full
            self.state.proposal_weights = {p.id: p.weights for p in PROFILES}
            self.state.audit.record(
                ctx.cop.clock_min,
                "system",
                "PROPOSAL",
                f"{trigger}: "
                + "; ".join(
                    f"COA {c.id} {c.name} cov {c.kpis.priority_coverage_pct}% changes {c.kpis.changes}"
                    for c in coas
                ),
            )
            logger.info(
                "proposal generated",
                extra=log_fields(
                    trigger=trigger,
                    candidates=len(ctx.full.candidates),
                    solve_ms=[c.solve_ms for c in coas],
                    recommended=recommended.id,
                ),
            )
            return proposal

    def approve(self, coa_id: str) -> Plan:
        with self.state.lock:
            proposal = self.state.proposal
            if proposal is None:
                raise ConflictError("There is no pending proposal to approve")
            coa = next((c for c in proposal.coas if c.id == coa_id), None)
            if coa is None:
                raise NotFoundError(f"COA {coa_id} is not in the pending proposal")
            if coa.violations:
                raise ConflictError(f"COA {coa_id} has {len(coa.violations)} constraint violations")
            self.state.plan = Plan(
                version=self.state.plan.version + 1,
                approved_coa=f"{coa.id} - {coa.name}",
                approved_at_min=self.state.cop.clock_min,
                packages=coa.packages,
            )
            self.state.plan_weights = self.state.proposal_weights.get(coa_id, PROFILES[0].weights)
            self.state.proposal = None
            self.state.audit.record(
                self.state.cop.clock_min,
                "commander",
                "APPROVE",
                f"Plan v{self.state.plan.version}: COA {coa.id} {coa.name}, {coa.kpis.sorties} sorties, "
                f"{coa.kpis.changes} changes",
            )
            return self.state.plan

    def reject(self) -> None:
        with self.state.lock:
            if self.state.proposal is None:
                raise ConflictError("There is no pending proposal to reject")
            self.state.audit.record(
                self.state.cop.clock_min,
                "commander",
                "REJECT",
                f"Proposal {self.state.proposal.id} ({self.state.proposal.trigger})",
            )
            self.state.proposal = None

    def explain(self, mission_id: str) -> MissionExplanation:
        with self.state.lock:
            cop = self.state.cop
            mission = next((m for m in cop.missions if m.id == mission_id), None)
            if mission is None:
                raise NotFoundError(f"Mission {mission_id} not found")
            package = next((p for p in self.state.plan.packages if p.mission_id == mission_id), None)
            return explain_mission(cop, mission, package, self.state.last_candidates, self.state.plan_weights)

    def current_unassigned(self) -> list[UnassignedMission]:
        with self.state.lock:
            if self.state.last_candidates is None:
                return []
            return unassigned(self.state.cop, self.state.plan.packages, self.state.last_candidates)

    def current_kpis(self) -> tuple[PlanKpis, list[str]]:
        with self.state.lock:
            violations = validate(self.state.cop, self.state.plan.packages)
            return compute_kpis(self.state.cop, self.state.plan.packages, 0, len(violations)), violations
