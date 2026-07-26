from datetime import datetime

import pandas as pd

from trading_agent.core.backtest import Backtester
from trading_agent.core.data import SyntheticData
from trading_agent.core.models import Signal
from trading_agent.core.risk import RiskLimits, RiskManager
from trading_agent.strategies.base import Strategy
from trading_agent.strategies.sma_crossover import SmaCrossover


def test_backtest_runs_and_reports():
    data = SyntheticData().history("TEST", datetime(2022, 1, 1), datetime(2024, 1, 1))
    bt = Backtester(SmaCrossover(fast=10, slow=30), RiskManager(RiskLimits()), starting_cash=10_000)
    result = bt.run("TEST", data)
    summary = result.summary()
    assert summary["final_equity"] > 0
    assert len(result.equity_curve) == len(data)
    assert "max_drawdown" in summary


def test_backtest_never_uses_leverage():
    # The broker must reject any buy that would overdraw cash, so cash stays >= 0
    # and the agent never trades on margin it doesn't have.
    data = SyntheticData().history("TEST", datetime(2022, 1, 1), datetime(2024, 1, 1))
    limits = RiskLimits(max_position_pct=0.10)
    bt = Backtester(SmaCrossover(fast=5, slow=15), RiskManager(limits), starting_cash=10_000)
    bt.run("TEST", data)
    assert bt.broker.cash >= 0
    assert bt.broker.account().equity > 0


class _AlwaysShort(Strategy):
    """Emits a steady bearish signal so the backtester opens (and holds) a short."""
    name = "always-short"
    warmup = 1

    def generate(self, symbol, history):
        return Signal(symbol, -0.8, history.index[-1], "test")


def test_backtest_can_open_a_short_when_enabled():
    idx = pd.date_range("2022-01-01", periods=20, freq="B")
    df = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0,
                       "close": 100.0, "volume": 1e6}, index=idx)
    bt = Backtester(_AlwaysShort(), RiskManager(RiskLimits(max_position_pct=0.10,
                    min_cash_pct=0.0)), starting_cash=10_000, slippage_bps=0,
                    allow_short=True)
    result = bt.run("TEST", df)
    assert any(t["side"] == "sell" for t in result.trades)  # opened a short
    assert bt.broker.positions()["TEST"].quantity < 0


def test_backtest_stays_long_only_by_default():
    idx = pd.date_range("2022-01-01", periods=20, freq="B")
    df = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0,
                       "close": 100.0, "volume": 1e6}, index=idx)
    bt = Backtester(_AlwaysShort(), RiskManager(RiskLimits()), starting_cash=10_000)
    bt.run("TEST", df)
    assert not bt.broker.positions()  # allow_short defaults off -> no short opened


def test_synthetic_data_is_reproducible():
    a = SyntheticData().history("TEST", datetime(2022, 1, 1), datetime(2023, 1, 1))
    b = SyntheticData().history("TEST", datetime(2022, 1, 1), datetime(2023, 1, 1))
    assert (a["close"].values == b["close"].values).all()
