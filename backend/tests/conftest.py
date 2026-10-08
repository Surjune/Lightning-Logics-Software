import pathlib
from collections.abc import Iterator

import pytest

from app.core.constants import SCENARIO_SEED
from app.domain.models import Cop
from app.scenario.generator import build_scenario
from app.services import planning
from app.services.audit import AuditLog
from app.services.container import Services, build_services

# A smaller search budget keeps the suite fast; plans are still optimised and validated.
TEST_SOLVER_BUDGET = 0.1


@pytest.fixture(autouse=True)
def fast_solver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(planning, "SOLVER_DETERMINISTIC_BUDGET", TEST_SOLVER_BUDGET)


@pytest.fixture
def cop() -> Cop:
    return build_scenario(SCENARIO_SEED)


@pytest.fixture
def services(tmp_path: pathlib.Path) -> Services:
    return build_services(AuditLog(tmp_path / "audit.jsonl"))


@pytest.fixture
def planned(services: Services) -> Iterator[Services]:
    """Services with an approved initial plan."""
    proposal = services.planning.generate("test: initial plan")
    services.planning.approve(proposal.recommended_coa_id)
    yield services
