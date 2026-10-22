"""Today's date for the user, in the America/Sao_Paulo time zone."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


class Clock(Protocol):
    def today(self) -> date: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SystemClock:
    def __init__(self, now: Callable[[], datetime] = _utc_now) -> None:
        self._now = now

    def today(self) -> date:
        return self._now().astimezone(SAO_PAULO).date()
