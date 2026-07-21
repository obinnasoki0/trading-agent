import queue
from datetime import datetime

from trading_agent.core.schedule import AutonomousRunner, Session


class RecordingEngine:
    def __init__(self):
        self.calls = []  # each entry is the `symbols` arg passed to step()

    def step(self, symbols=None):
        self.calls.append(symbols)
        return [f"evaluated {symbols or 'ALL'}"]


def test_event_wakes_loop_for_specific_symbol():
    q = queue.Queue()
    q.put("AAPL")  # a headline event arrived before the second cycle
    engine = RecordingEngine()
    runner = AutonomousRunner(engine, interval_seconds=999, session=Session.ALWAYS,
                              max_iterations=2, sleeper=lambda _s: None, event_queue=q)
    list(runner.run())
    # First cycle evaluates all; second is event-driven for AAPL only.
    assert engine.calls[0] is None
    assert engine.calls[1] == ["AAPL"]


def test_multiple_events_are_coalesced():
    q = queue.Queue()
    for sym in ("AAPL", "MSFT", "AAPL"):
        q.put(sym)
    engine = RecordingEngine()
    runner = AutonomousRunner(engine, interval_seconds=999, session=Session.ALWAYS,
                              max_iterations=2, sleeper=lambda _s: None, event_queue=q)
    list(runner.run())
    assert engine.calls[1] == ["AAPL", "MSFT"]  # deduped + sorted


def test_no_queue_falls_back_to_timed_cycles():
    engine = RecordingEngine()
    slept = []
    runner = AutonomousRunner(engine, interval_seconds=42, session=Session.ALWAYS,
                              max_iterations=2, sleeper=lambda s: slept.append(s))
    list(runner.run())
    assert engine.calls == [None, None]   # always full cycles
    assert slept == [42]                  # slept once between the two cycles
