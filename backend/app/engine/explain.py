"""Plain-language explanations: what changed, why, and why not."""

from collections import Counter

from app.core.clock import fmt_clock
from app.domain.enums import AircraftStatus, ChangeKind, MissionOrigin, RejectReason, WeatherCondition
from app.domain.models import Cop, Mission
from app.domain.plan import (
    CandidateView,
    Change,
    MissionExplanation,
    Package,
    RejectTally,
    UnassignedMission,
)
from app.engine import prediction
from app.engine.candidates import CandidateSet
from app.engine.optimizer import CoaWeights, candidate_score

REASON_TEXT = {
    RejectReason.AIRCRAFT_UNSERVICEABLE: "aircraft unserviceable",
    RejectReason.SERVICEABILITY_RISK: "predicted serviceability too low",
    RejectReason.BASE_UNAVAILABLE: "launch base closed or below minima",
    RejectReason.ROLE_MISMATCH: "aircraft not cleared for the role",
    RejectReason.NOT_NIGHT_CAPABLE: "aircraft not night-capable",
    RejectReason.NO_SUITABLE_WEAPON: "no effective weapon in the loadouts",
    RejectReason.NO_WEAPON_STOCK: "weapon stock exhausted at base",
    RejectReason.TARGET_WEATHER: "target weather rules out the weapon",
    RejectReason.ROUTE_BLOCKED: "no route clear of storms and restricted airspace",
    RejectReason.OUT_OF_RANGE: "target beyond combat radius",
    RejectReason.TIME_WINDOW: "cannot reach the target inside its window",
    RejectReason.MAINTENANCE_DUE: "scheduled maintenance due",
    RejectReason.NO_QUALIFIED_CREW: "no rested, qualified crew",
}
TOP_CANDIDATES_SHOWN = 6


def tallies(cset: CandidateSet, mission_id: str) -> list[RejectTally]:
    found = cset.rejections.get(mission_id, {})
    return [
        RejectTally(reason=r, count=n, example=ex)
        for r, (n, ex) in sorted(found.items(), key=lambda kv: -kv[1][0])
    ]


def _crew_label(cop: Cop, crew_id: str) -> str:
    return next((c.callsign for c in cop.crews if c.id == crew_id), crew_id)


def _labels(cop: Cop, p: Package | None) -> list[str]:
    if p is None:
        return []
    return [f"{s.aircraft_tail} ({_crew_label(cop, s.crew_id)})" for s in p.sorties]


def _why_released(cop: Cop, tail: str, now_tasked: dict[str, Mission]) -> str:
    a = next(a for a in cop.aircraft if a.tail == tail)
    if a.status == AircraftStatus.UNSERVICEABLE:
        return f"{tail} unserviceable ({a.status_note or 'defect'})"
    base_reason = prediction.base_unavailable_reason(cop.base(a.base_id), cop.weather)
    if base_reason:
        return f"{tail}: {base_reason}"
    if tail in now_tasked:
        m = now_tasked[tail]
        return f"{tail} re-tasked to {m.id} {m.name} (priority {m.priority})"
    return f"{tail} released: a better-scoring option was found"


def diff_plans(
    cop: Cop, previous: list[Package], proposed: list[Package], full: CandidateSet
) -> list[Change]:
    missions = {m.id: m for m in cop.missions}
    before = {p.mission_id: p for p in previous}
    after = {p.mission_id: p for p in proposed}
    now_tasked = {s.aircraft_tail: missions[p.mission_id] for p in proposed for s in p.sorties}
    changes: list[Change] = []
    for mid in sorted(set(before) | set(after)):
        m = missions.get(mid)
        b, a = before.get(mid), after.get(mid)
        if m is None or (b is not None and a is not None and b == a):
            continue
        if a is None and b is not None:
            if not full.by_mission.get(mid):
                top = tallies(full, mid)
                why = REASON_TEXT[top[0].reason] if top else "no feasible option"
                reason = f"No feasible option now: {why}"
            else:
                moved = sorted(
                    {now_tasked[s.aircraft_tail].id for s in b.sorties if s.aircraft_tail in now_tasked}
                )
                reason = (
                    f"Assets re-tasked to higher-value missions ({', '.join(moved)})"
                    if moved
                    else "Dropped to free assets for higher-value tasking"
                )
            changes.append(
                Change(
                    mission_id=mid, kind=ChangeKind.REMOVED, before=_labels(cop, b), after=[], reason=reason
                )
            )
        elif b is None and a is not None:
            reason = {
                MissionOrigin.TST: "New time-sensitive target",
                MissionOrigin.AUTO_SEAD: "SEAD tasking against a newly detected SAM",
            }.get(m.origin, "Resourced with assets freed by the retask")
            changes.append(
                Change(mission_id=mid, kind=ChangeKind.ADDED, before=[], after=_labels(cop, a), reason=reason)
            )
        elif a is not None and b is not None:
            b_tails = {s.aircraft_tail for s in b.sorties}
            a_tails = {s.aircraft_tail for s in a.sorties}
            if b_tails != a_tails:
                reasons = [_why_released(cop, t, now_tasked) for t in sorted(b_tails - a_tails)]
                changes.append(
                    Change(
                        mission_id=mid,
                        kind=ChangeKind.REASSIGNED,
                        before=_labels(cop, b),
                        after=_labels(cop, a),
                        reason="; ".join(reasons),
                    )
                )
            elif {s.crew_id for s in b.sorties} != {s.crew_id for s in a.sorties}:
                gone = [
                    c
                    for c in cop.crews
                    if c.id in {s.crew_id for s in b.sorties} - {s.crew_id for s in a.sorties}
                ]
                reason = "; ".join(
                    f"{c.callsign}: {c.status_note or 'reassigned to balance crew duty'}" for c in gone
                )
                changes.append(
                    Change(
                        mission_id=mid,
                        kind=ChangeKind.REASSIGNED,
                        before=_labels(cop, b),
                        after=_labels(cop, a),
                        reason=f"Crew change. {reason}",
                    )
                )
            elif b.tot_min != a.tot_min:
                changes.append(
                    Change(
                        mission_id=mid,
                        kind=ChangeKind.RETIMED,
                        before=_labels(cop, b),
                        after=_labels(cop, a),
                        reason=f"Time on target {fmt_clock(b.tot_min)} -> {fmt_clock(a.tot_min)} "
                        f"to deconflict with the revised plan",
                    )
                )
    return changes


def tasking_changes(previous: list[Package], proposed: list[Package]) -> int:
    """Number of aircraft whose tasking differs between two plans."""
    before = {(s.aircraft_tail, p.mission_id) for p in previous for s in p.sorties}
    after = {(s.aircraft_tail, p.mission_id) for p in proposed for s in p.sorties}
    return len(before ^ after)


def unassigned(cop: Cop, proposed: list[Package], full: CandidateSet) -> list[UnassignedMission]:
    covered = {p.mission_id for p in proposed}
    out = []
    for m in sorted(cop.missions, key=lambda m: -m.priority):
        if m.id in covered:
            continue
        options = full.by_mission.get(m.id, [])
        found = tallies(full, m.id)
        if not options:
            why = ", ".join(REASON_TEXT[t.reason] for t in found[:2]) or "no aircraft can fly this role"
            summary = f"No feasible option: {why}"
        else:
            aircraft = len({c.tail for c in options})
            summary = (
                f"{aircraft} aircraft could fly it, but they are committed to higher-value missions "
                f"at the same time"
            )
        out.append(UnassignedMission(mission_id=m.id, summary=summary, rejections=found))
    return out


def explain_mission(
    cop: Cop, m: Mission, package: Package | None, full: CandidateSet | None, weights: CoaWeights
) -> MissionExplanation:
    factors: list[str] = []
    options = full.by_mission.get(m.id, []) if full else []
    chosen = {s.aircraft_tail: s.weapon_code for s in package.sorties} if package else {}
    if package:
        types = Counter(s.type_code for s in package.sorties)
        loads = Counter(f"{s.weapon_qty}x {s.weapon_code}" for s in package.sorties)
        headline = (
            f"{len(package.sorties)}-ship package, time on target {fmt_clock(package.tot_min)}: "
            + ", ".join(f"{n}x {t}" for t, n in types.items())
        )
        factors.append("Loadouts: " + ", ".join(f"{n} aircraft with {lo}" for lo, n in loads.items()))
        factors.append(
            f"Expected effect {package.expected_effect:.0%}; expected losses {package.risk:.2f} aircraft"
        )
        worst = max(package.sorties, key=lambda s: s.exposure_km)
        if worst.exposure_km > 0:
            factors.append(
                f"Worst route exposure: {worst.exposure_km:.0f} lethality-weighted km inside SAM cover"
            )
        else:
            factors.append("Routes stay outside all known SAM cover")
        standoff = max(cop.weapon(s.weapon_code).standoff_km for s in package.sorties)
        if standoff > 0:
            factors.append(f"Weapons released up to {standoff:.0f} km short of the target")
        if any(s.needs_aar for s in package.sorties):
            factors.append("Needs air-to-air refuelling to reach the release point")
    else:
        headline = "Not resourced in this plan"
    if prediction.weather_at(m.position, cop.weather) != WeatherCondition.CLEAR:
        factors.append("Target under cloud: laser-guided and unguided weapons excluded")
    if prediction.overlaps_night(m.window_start_min, m.window_end_min):
        factors.append("Night window: night-capable aircraft and night-qualified crews only")
    if m.origin == MissionOrigin.TST:
        factors.append("Time-sensitive target raised during execution")

    ranked = sorted(options, key=lambda c: -candidate_score(c, weights))[:TOP_CANDIDATES_SHOWN]
    top = [
        CandidateView(
            aircraft_tail=c.tail,
            type_code=c.type_code,
            base_id=c.base_id,
            loadout=f"{c.qty}x {c.weapon_code}",
            p_success=round(c.p_success, 3),
            risk=round(c.risk, 3),
            score=round(candidate_score(c, weights), 1),
            selected=chosen.get(c.tail) == c.weapon_code,
        )
        for c in ranked
    ]
    return MissionExplanation(
        mission_id=m.id,
        headline=headline,
        factors=factors,
        candidates_considered=len(options),
        top_candidates=top,
        rejections=tallies(full, m.id) if full else [],
    )
