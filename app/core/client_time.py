"""The user's calendar, not the server's.

A food diary is organised by the day the user lived: a meal logged at 00:30 in
Rome belongs to the new Italian day even though it is still yesterday in UTC.
The app sends its IANA zone in the ``X-Timezone`` header on every request;
``get_current_user`` resolves it (falling back to the zone stored on the user,
then UTC) and activates it for the request with :func:`use_timezone`.

Everything that means "today" or "the day of this instant" goes through this
module instead of ``dt.date.today()`` / ``created_at.date()``, which follow the
server clock.
"""

import contextlib
import datetime as dt
from collections.abc import Iterator
from contextvars import ContextVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC_ZONE = ZoneInfo("UTC")
TIMEZONE_HEADER = "X-Timezone"

_current_zone: ContextVar[ZoneInfo] = ContextVar("client_timezone", default=UTC_ZONE)


def parse_timezone(value: str | None) -> ZoneInfo | None:
    """Return the IANA zone named by ``value``, or None when it is not one."""
    if not value:
        return None
    name = value.strip()
    if not name or len(name) > 64:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def set_timezone(zone: ZoneInfo) -> None:
    """Activate ``zone`` for the rest of the current request or task."""
    _current_zone.set(zone)


@contextlib.contextmanager
def use_timezone(zone: ZoneInfo | str | None) -> Iterator[ZoneInfo]:
    """Activate a zone for a block (background jobs, tests); UTC if unknown."""
    resolved = zone if isinstance(zone, ZoneInfo) else parse_timezone(zone) or UTC_ZONE
    token = _current_zone.set(resolved)
    try:
        yield resolved
    finally:
        _current_zone.reset(token)


def current_timezone() -> ZoneInfo:
    return _current_zone.get()


def client_now() -> dt.datetime:
    """The current instant, expressed in the user's zone."""
    return dt.datetime.now(dt.UTC).astimezone(current_timezone())


def client_today() -> dt.date:
    """Today's date on the user's calendar."""
    return client_now().date()


def local_date(instant: dt.datetime, zone: ZoneInfo | None = None) -> dt.date:
    """The user's calendar day for ``instant``. Naive values are UTC (SQLite)."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=dt.UTC)
    return instant.astimezone(zone or current_timezone()).date()


def moved_to_day(instant: dt.datetime, day: dt.date, zone: ZoneInfo | None = None) -> dt.datetime:
    """``instant`` moved to ``day`` at the same local wall-clock time, in UTC.

    Moving a 20:30 dinner to yesterday keeps it at 20:30 on the user's clock,
    whatever the UTC offset (or DST change) between the two days.
    """
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=dt.UTC)
    zone = zone or current_timezone()
    wall_clock = instant.astimezone(zone).time()
    return dt.datetime.combine(day, wall_clock, tzinfo=zone).astimezone(dt.UTC)
