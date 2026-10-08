"""Turns solver assignments into the plan's packages and sorties."""

from collections import defaultdict

from app.domain.plan import Package, Sortie
from app.engine.optimizer import Assignment


def to_packages(assignments: list[Assignment]) -> list[Package]:
    by_mission: dict[str, list[Assignment]] = defaultdict(list)
    for a in assignments:
        by_mission[a.candidate.mission_id].append(a)
    packages: list[Package] = []
    for mission_id, group in by_mission.items():
        sorties = [
            Sortie(
                aircraft_tail=a.candidate.tail,
                type_code=a.candidate.type_code,
                crew_id=a.crew_id,
                base_id=a.candidate.base_id,
                weapon_code=a.candidate.weapon_code,
                weapon_qty=a.candidate.qty,
                launch_min=a.tot_min - a.candidate.pre_min,
                tot_min=a.tot_min,
                recover_min=a.tot_min + a.candidate.post_min,
                route=list(a.candidate.route),
                route_km=round(a.candidate.route_km, 1),
                exposure_km=round(a.candidate.exposure_km, 1),
                risk=round(a.candidate.risk, 4),
                effect=round(a.candidate.effect, 4),
                p_success=round(a.candidate.p_success, 4),
                needs_aar=a.candidate.needs_aar,
            )
            for a in sorted(group, key=lambda a: a.candidate.tail)
        ]
        packages.append(
            Package(
                mission_id=mission_id,
                tot_min=group[0].tot_min,
                sorties=sorties,
                expected_effect=round(sum(s.p_success for s in sorties) / len(sorties), 4),
                risk=round(sum(s.risk for s in sorties), 4),
            )
        )
    return sorted(packages, key=lambda p: (p.tot_min, p.mission_id))
