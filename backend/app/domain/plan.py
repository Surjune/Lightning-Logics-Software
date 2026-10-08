"""Air plan structures: sorties, packages, courses of action and proposals."""

from pydantic import BaseModel, Field

from app.core.geometry import Point
from app.domain.enums import ChangeKind, PackageStatus, RejectReason


class Sortie(BaseModel):
    aircraft_tail: str
    type_code: str
    crew_id: str
    base_id: str
    weapon_code: str
    weapon_qty: int
    launch_min: int
    tot_min: int  # time on target (or on CAP station)
    recover_min: int
    route: list[Point]  # base to weapon-release point (or CAP station)
    route_km: float
    exposure_km: float  # lethality-weighted km inside SAM coverage, out and back
    risk: float  # probability of losing this aircraft on this sortie
    effect: float  # probability this aircraft's weapons achieve the effect
    p_success: float  # effect x serviceability x survival
    needs_aar: bool


class Package(BaseModel):
    mission_id: str
    tot_min: int
    status: PackageStatus = PackageStatus.PLANNED
    sorties: list[Sortie]
    expected_effect: float  # mean p_success across the package
    risk: float  # expected aircraft losses for the package


class PlanKpis(BaseModel):
    missions_total: int
    missions_covered: int
    priority_coverage_pct: float
    expected_effect_pct: float
    expected_losses: float
    aircraft_used: int
    aircraft_available: int
    utilisation_pct: float
    sorties: int
    standoff_rounds: int
    changes: int
    violations: int


class Change(BaseModel):
    mission_id: str
    kind: ChangeKind
    before: list[str]
    after: list[str]
    reason: str


class RejectTally(BaseModel):
    reason: RejectReason
    count: int
    example: str


class UnassignedMission(BaseModel):
    mission_id: str
    summary: str
    rejections: list[RejectTally] = Field(default_factory=list)


class Coa(BaseModel):
    id: str
    name: str
    rationale: str
    packages: list[Package]
    unassigned: list[UnassignedMission]
    kpis: PlanKpis
    changes: list[Change]
    violations: list[str]
    solver_status: str
    solve_ms: int


class Proposal(BaseModel):
    id: str
    trigger: str
    created_min: int
    coas: list[Coa]
    baseline_kpis: PlanKpis
    recommended_coa_id: str


class Plan(BaseModel):
    version: int = 0
    approved_coa: str | None = None
    approved_at_min: int | None = None
    packages: list[Package] = Field(default_factory=list)


class CandidateView(BaseModel):
    """One option the planner weighed for a mission, for the explanation panel."""

    aircraft_tail: str
    type_code: str
    base_id: str
    loadout: str
    p_success: float
    risk: float
    score: float
    selected: bool


class MissionExplanation(BaseModel):
    mission_id: str
    headline: str
    factors: list[str]
    candidates_considered: int
    top_candidates: list[CandidateView]
    rejections: list[RejectTally]
