from app.domain.enums import PackageStatus
from app.domain.plan import Package
from app.services.container import Services


def _first(packages: list[Package]) -> Package:
    return min(packages, key=lambda p: min(s.launch_min for s in p.sorties))


def test_advancing_launches_packages_and_debits_resources(planned: Services) -> None:
    before = planned.picture.snapshot()
    first = _first(before.plan.packages)
    sortie = first.sorties[0]
    stock_before = before.cop.stock(sortie.base_id, sortie.weapon_code)
    used = sum(
        s.weapon_qty
        for s in first.sorties
        if (s.base_id, s.weapon_code) == (sortie.base_id, sortie.weapon_code)
    )

    planned.clock.advance(min(s.launch_min for s in first.sorties) + 1)
    after = planned.picture.snapshot()
    pkg = next(p for p in after.plan.packages if p.mission_id == first.mission_id)
    assert pkg.status in (PackageStatus.AIRBORNE, PackageStatus.COMPLETE)
    assert after.cop.stock(sortie.base_id, sortie.weapon_code) <= stock_before - used
    crew = next(c for c in after.cop.crews if c.id == sortie.crew_id)
    assert crew.sorties_today == 1


def test_airborne_packages_are_frozen_in_retask(planned: Services) -> None:
    first = _first(planned.picture.snapshot().plan.packages)
    planned.clock.advance(min(s.launch_min for s in first.sorties) + 1)
    proposal = planned.planning.generate("test")
    for coa in proposal.coas:
        kept = next(p for p in coa.packages if p.mission_id == first.mission_id)
        assert [s.aircraft_tail for s in kept.sorties] == [s.aircraft_tail for s in first.sorties]
