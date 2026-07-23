from datetime import datetime

import pandas as pd

from trading_agent.core.engine import TradingEngine
from trading_agent.core.models import Signal
from trading_agent.core.risk import RiskLimits, RiskManager
from trading_agent.core.universe import resolve
from trading_agent.strategies.base import Strategy


def test_resolve_universe_presets_and_lists():
    assert "AAPL" in resolve("largecap")
    assert "BTC/USD" in resolve("crypto")
    assert resolve(["X", "Y"]) == ["X", "Y"]
    assert resolve("A, B ,C") == ["A", "B", "C"]


class _ScoreByName(Strategy):
    """Signal strength keyed off a lookup so we can control the ranking."""
    name = "byname"
    warmup = 1

    def __init__(self, scores):
        self.scores = scores

    def generate(self, symbol, history):
        return Signal(symbol, self.scores.get(symbol, 0.0), history.index[-1], symbol)


class _FakeData:
    def history(self, symbol, start, end):
        idx = pd.date_range("2022-01-01", periods=3, freq="B")
        return pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0,
                             "close": 100.0, "volume": 1e6}, index=idx)


def _engine(scores, max_positions):
    from trading_agent.brokers.paper import PaperBroker
    broker = PaperBroker(starting_cash=10_000, slippage_bps=0)
    rm = RiskManager(RiskLimits(max_position_pct=0.10, min_cash_pct=0.0))
    return TradingEngine(broker, _ScoreByName(scores), rm, _FakeData(),
                         symbols=["A", "B", "C", "D"], lookback_days=10,
                         max_positions=max_positions), broker


def test_ranking_holds_only_top_n():
    # A and C are strongest; with 2 slots only those two should be bought.
    scores = {"A": 0.9, "B": 0.1, "C": 0.7, "D": 0.2}
    engine, broker = _engine(scores, max_positions=2)
    engine.step()
    held = set(broker.positions())
    assert held == {"A", "C"}


def test_no_cap_buys_all_qualifying():
    scores = {"A": 0.9, "B": 0.9, "C": 0.9, "D": 0.9}
    engine, broker = _engine(scores, max_positions=0)  # 0 = uncapped path
    engine.step()
    assert len(broker.positions()) == 4
