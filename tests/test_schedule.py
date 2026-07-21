from datetime import datetime

from trading_agent.core.schedule import AutonomousRunner, Session, is_market_open


def test_always_session_is_always_open():
    assert is_market_open(Session.ALWAYS, datetime(2026, 1, 1, 3, 0))  # New Year 3am
    assert is_market_open(Session.ALWAYS, datetime(2026, 7, 19, 23, 0))  # a Sunday


def test_equity_closed_on_weekend():
    # 2026-07-18 is a Saturday.
    assert not is_market_open(Session.EQUITY, datetime(2026, 7, 18, 12, 0))


def test_equity_hours_weekday():
    # 2026-07-20 is a Monday.
    assert is_market_open(Session.EQUITY, datetime(2026, 7, 20, 10, 0))
    assert not is_market_open(Session.EQUITY, datetime(2026, 7, 20, 3, 0))
    assert not is_market_open(Session.EQUITY, datetime(2026, 7, 20, 20, 0))


class _FakeEngine:
    def __init__(self):
        self.calls = 0

    def step(self, symbols=None):
        self.calls += 1
        return [f"step {self.calls}"]


def test_runner_steps_when_open_and_stops():
    engine = _FakeEngine()
    runner = AutonomousRunner(engine, interval_seconds=0, session=Session.ALWAYS,
                              max_iterations=3, sleeper=lambda _s: None)
    logs = list(runner.run())
    assert len(logs) == 3
    assert engine.calls == 3


def test_runner_idles_when_closed():
    engine = _FakeEngine()
    # Force a weekend clock so the equity market is closed.
    runner = AutonomousRunner(engine, interval_seconds=0, session=Session.EQUITY,
                              max_iterations=2, sleeper=lambda _s: None,
                              clock=lambda: datetime(2026, 7, 18, 12, 0))
    logs = list(runner.run())
    assert engine.calls == 0
    assert all("closed" in a[1][0] for a in logs)
