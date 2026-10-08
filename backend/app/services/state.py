"""The single in-memory operational state, guarded by one lock.

This is the only place the picture and plan are held; services read and write it under
the lock. A database-backed store would replace this class without changing services.
"""

import threading
from dataclasses import dataclass, field

from app.core.constants import COA_BALANCED
from app.domain.models import Cop
from app.domain.plan import Plan, Proposal
from app.engine.candidates import CandidateSet
from app.engine.optimizer import CoaWeights
from app.engine.routing import RouterCache
from app.scenario.generator import build_scenario
from app.services.audit import AuditLog


@dataclass
class OpsState:
    cop: Cop
    audit: AuditLog
    plan: Plan = field(default_factory=Plan)
    proposal: Proposal | None = None
    routers: RouterCache = field(default_factory=RouterCache)
    # Candidate set and weights behind the current plan, for explanations.
    last_candidates: CandidateSet | None = None
    plan_weights: CoaWeights = field(default_factory=lambda: CoaWeights(*COA_BALANCED))
    # Weights behind each COA of the pending proposal, keyed by COA id.
    proposal_weights: dict[str, CoaWeights] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)

    @classmethod
    def fresh(cls, seed: int, audit: AuditLog) -> "OpsState":
        return cls(cop=build_scenario(seed), audit=audit)

    def reset(self, seed: int) -> None:
        with self.lock:
            self.cop = build_scenario(seed)
            self.plan = Plan()
            self.proposal = None
            self.routers = RouterCache()
            self.last_candidates = None
            self.plan_weights = CoaWeights(*COA_BALANCED)
            self.proposal_weights = {}
