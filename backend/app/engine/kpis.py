from app.domain.enums import AircraftStatus, WeaponKind
from app.domain.models import Cop
from app.domain.plan import Package, PlanKpis


def compute_kpis(cop: Cop, packages: list[Package], changes: int, violations: int) -> PlanKpis:
    missions = {m.id: m for m in cop.missions}
    total_priority = sum(m.priority for m in cop.missions) or 1
    covered = [p for p in packages if p.mission_id in missions]
    covered_priority = sum(missions[p.mission_id].priority for p in covered)
    effect = sum(missions[p.mission_id].priority * p.expected_effect for p in covered)
    used = {s.aircraft_tail for p in packages for s in p.sorties}
    available = [
        a
        for a in cop.aircraft
        if a.status == AircraftStatus.SERVICEABLE and not cop.aircraft_type(a.type_code).is_tanker
    ]
    standoff = {w.code for w in cop.weapons if w.kind == WeaponKind.STANDOFF}
    return PlanKpis(
        missions_total=len(cop.missions),
        missions_covered=len(covered),
        priority_coverage_pct=round(100 * covered_priority / total_priority, 1),
        expected_effect_pct=round(100 * effect / total_priority, 1),
        expected_losses=round(sum(p.risk for p in packages), 2),
        aircraft_used=len(used),
        aircraft_available=len(available),
        utilisation_pct=round(100 * len(used) / max(len(available), 1), 1),
        sorties=sum(len(p.sorties) for p in packages),
        standoff_rounds=sum(s.weapon_qty for p in packages for s in p.sorties if s.weapon_code in standoff),
        changes=changes,
        violations=violations,
    )
