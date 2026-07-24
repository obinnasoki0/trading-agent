"""An unattended loop must survive transient errors (broker 500s, network
blips) and keep running -- not crash. This guards that."""

from datetime import datetime

from trading_agent.core.schedule import AutonomousRunner, Session


class _FlakyEngine:
    """Raises on the first cycle, succeeds after -- like a transient API error."""

    def __init__(self):
        self.calls = 0

    def step(self, symbols=None):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("500 Internal Server Error")
        return [f"ok {self.calls}"]


def test_loop_survives_a_failing_cycle():
    engine = _FlakyEngine()
    runner = AutonomousRunner(engine, interval_seconds=0, session=Session.ALWAYS,
                              max_iterations=3, sleeper=lambda _s: None)
    logs = list(runner.run())
    assert len(logs) == 3                       # did not crash on the first failure
    assert "cycle error" in logs[0][1][0]       # first cycle logged the error
    assert logs[1][1] == ["ok 2"]               # recovered on the next cycle
    assert engine.calls == 3                     # kept stepping
