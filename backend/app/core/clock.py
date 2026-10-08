from app.core.constants import DAY_START_CLOCK_MIN

MINUTES_PER_HOUR = 60
HOURS_PER_DAY = 24


def fmt_clock(minute: int) -> str:
    """Scenario minute to local 24-hour clock time, e.g. 95 -> '06:35'."""
    total = DAY_START_CLOCK_MIN + minute
    return f"{total // MINUTES_PER_HOUR % HOURS_PER_DAY:02d}:{total % MINUTES_PER_HOUR:02d}"
