"""Simulation clock: launches and recovers packages as scenario time advances."""

from app.core.clock import fmt_clock
from app.core.constants import ARMING_LEAD_TIME_MIN, HORIZON_MIN
from app.domain.enums import PackageStatus, SourceSystem
from app.domain.models import Cop, FeedEntry
from app.domain.plan import Package
from app.services.state import OpsState

MINUTES_PER_HOUR = 60


def _debit_launch(cop: Cop, p: Package) -> None:
    """At launch, flying time, crew duty and weapons are drawn from the picture."""
    aircraft = {a.tail: a for a in cop.aircraft}
    crews = {c.id: c for c in cop.crews}
    for s in p.sorties:
        minutes = s.recover_min - s.launch_min
        a = aircraft[s.aircraft_tail]
        a.sorties_today += 1
        a.hours_to_maintenance = round(max(a.hours_to_maintenance - minutes / MINUTES_PER_HOUR, 0.0), 1)
        crew = crews[s.crew_id]
        crew.sorties_today += 1
        crew.duty_min_used += minutes
        stock = next(x for x in cop.stocks if x.base_id == s.base_id and x.weapon_code == s.weapon_code)
        stock.quantity = max(stock.quantity - s.weapon_qty, 0)


class ClockService:
    def __init__(self, state: OpsState) -> None:
        self.state = state

    def advance(self, minutes: int) -> list[FeedEntry]:
        with self.state.lock:
            cop = self.state.cop
            feed_before = len(cop.feed)
            cop.clock_min = min(cop.clock_min + minutes, HORIZON_MIN)
            now = cop.clock_min
            updated: list[Package] = []
            for p in self.state.plan.packages:
                launch = min(s.launch_min for s in p.sorties)
                recover = max(s.recover_min for s in p.sorties)
                status = p.status
                if status in (PackageStatus.PLANNED, PackageStatus.COMMITTED) and launch <= now:
                    _debit_launch(cop, p)
                    status = PackageStatus.AIRBORNE
                    cop.feed.append(
                        FeedEntry(
                            at_min=now,
                            source=SourceSystem.OPS,
                            summary=f"{p.mission_id} airborne: {len(p.sorties)} aircraft "
                            f"(launched {fmt_clock(launch)})",
                        )
                    )
                if status == PackageStatus.AIRBORNE and recover <= now:
                    status = PackageStatus.COMPLETE
                    cop.feed.append(
                        FeedEntry(
                            at_min=now,
                            source=SourceSystem.OPS,
                            summary=f"{p.mission_id} recovered ({fmt_clock(recover)})",
                        )
                    )
                elif status == PackageStatus.PLANNED and launch <= now + ARMING_LEAD_TIME_MIN:
                    status = PackageStatus.COMMITTED
                updated.append(p.model_copy(update={"status": status}))
            self.state.plan.packages = updated
            if self.state.proposal is not None:
                cop.feed.append(
                    FeedEntry(
                        at_min=now,
                        source=SourceSystem.OPS,
                        summary=f"Pending proposal '{self.state.proposal.trigger}' expired "
                        f"as the clock moved on",
                    )
                )
                self.state.proposal = None
            self.state.audit.record(now, "commander", "CLOCK", f"Advanced {minutes} min to {fmt_clock(now)}")
            return cop.feed[feed_before:]
