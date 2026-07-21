"""Market sessions and the autonomous run loop.

This is what makes the agent run unattended ("no human authorization"): the
``AutonomousRunner`` calls ``engine.step()`` on a fixed cadence, forever, and
only acts while the relevant market session is open. Its only gate is the
*automated* risk kill switch inside the engine -- there is no human approval
step, by design.

Sessions:
* ``EQUITY``   -- US regular hours, 09:30-16:00 ET, weekdays.
* ``EXTENDED`` -- pre/post market, 04:00-20:00 ET, weekdays.
* ``ALWAYS``   -- 24/7 (crypto). True round-the-clock trading lives here.
"""

from __future__ import annotations

import time
from datetime import datetime, time as dtime
from enum import Enum

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - zoneinfo always present on 3.9+
    _ET = None


class Session(str, Enum):
    EQUITY = "equity"
    EXTENDED = "extended"
    ALWAYS = "always"   # crypto / 24-7


_WINDOWS = {
    Session.EQUITY: (dtime(9, 30), dtime(16, 0)),
    Session.EXTENDED: (dtime(4, 0), dtime(20, 0)),
}


def is_market_open(session: Session, now: datetime | None = None) -> bool:
    if session is Session.ALWAYS:
        return True
    now = now or (datetime.now(_ET) if _ET else datetime.now())
    if _ET and now.tzinfo is None:
        now = now.replace(tzinfo=_ET)
    if now.weekday() >= 5:  # Sat/Sun -> equities closed
        return False
    start, end = _WINDOWS[session]
    return start <= now.time() <= end


class AutonomousRunner:
    """Runs an engine step on an interval, unattended, respecting the session.

    Parameters
    ----------
    interval_seconds : seconds to sleep between decision cycles.
    session          : which market clock to respect.
    max_iterations   : stop after N cycles (None = forever). Used by tests.
    sleeper          : injected for tests; defaults to time.sleep.
    """

    def __init__(self, engine, interval_seconds: int = 900,
                 session: Session = Session.EQUITY, max_iterations: int | None = None,
                 sleeper=time.sleep, clock=None):
        self.engine = engine
        self.interval = interval_seconds
        self.session = session
        self.max_iterations = max_iterations
        self._sleep = sleeper
        self._clock = clock or (lambda: datetime.now(_ET) if _ET else datetime.now())
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self):
        """Blocking autonomous loop. Yields the action log from each cycle."""
        iterations = 0
        while not self._stop:
            if self.max_iterations is not None and iterations >= self.max_iterations:
                break
            now = self._clock()
            if is_market_open(self.session, now):
                actions = self.engine.step()
                yield now, actions
            else:
                yield now, ["(market closed; idle)"]
            iterations += 1
            if self.max_iterations is not None and iterations >= self.max_iterations:
                break
            if not self._stop:
                self._sleep(self.interval)
