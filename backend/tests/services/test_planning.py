from app.domain.enums import AircraftStatus, PackageStatus
from app.domain.events import AircraftUnserviceableEvent
from app.services.container import Services


def test_initial_proposal_has_three_valid_coas(services: Services) -> None:
    proposal = services.planning.generate("test")
    assert [c.id for c in proposal.coas] == ["A", "B", "C"]
    for coa in proposal.coas:
        assert coa.violations == []
        assert coa.kpis.priority_coverage_pct >= proposal.baseline_kpis.priority_coverage_pct
    assert proposal.recommended_coa_id in {"A", "B", "C"}


def test_retask_after_grounding_never_uses_grounded_aircraft(planned: Services) -> None:
    plan = planned.picture.snapshot().plan
    target = next(p for p in plan.packages if p.status == PackageStatus.PLANNED)
    grounded = target.sorties[0].aircraft_tail
    _, proposal = planned.events.apply(AircraftUnserviceableEvent(tails=[grounded]))
    assert proposal is not None
    for coa in proposal.coas:
        assert coa.violations == []
        assert grounded not in {s.aircraft_tail for p in coa.packages for s in p.sorties}
        assert any(c.mission_id == target.mission_id for c in coa.changes)
    aircraft = {a.tail: a for a in planned.picture.snapshot().cop.aircraft}
    assert aircraft[grounded].status == AircraftStatus.UNSERVICEABLE


def test_retask_with_no_change_keeps_the_plan(planned: Services) -> None:
    proposal = planned.planning.generate("test: nothing changed")
    balanced = next(c for c in proposal.coas if c.id == "A")
    assert balanced.kpis.changes == 0


def test_explanation_names_package(planned: Services) -> None:
    package = planned.picture.snapshot().plan.packages[0]
    explanation = planned.planning.explain(package.mission_id)
    assert explanation.headline.startswith(f"{len(package.sorties)}-ship")
    assert explanation.factors
