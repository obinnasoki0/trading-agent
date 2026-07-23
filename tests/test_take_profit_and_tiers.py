import pandas as pd

from trading_agent.core.backtest import Backtester
from trading_agent.core.models import Signal
from trading_agent.core.risk import RiskLimits, RiskManager, RiskTier
from trading_agent.strategies.base import Strategy


class _AlwaysBuy(Strategy):
    name = "always_buy"
    warmup = 1

    def generate(self, symbol, history):
        return Signal(symbol, 1.0, history.index[-1], "buy")


def _rising_series():
    idx = pd.date_range("2022-01-03", periods=5, freq="B")
    px = [100, 105, 110, 115, 120]
    return pd.DataFrame({"open": px, "high": px, "low": px, "close": px,
                         "volume": [1_000_000] * 5}, index=idx)


def test_take_profit_exits_on_gain():
    limits = RiskLimits(max_position_pct=1.0, stop_loss_pct=0.5,
                        take_profit_pct=0.10, min_cash_pct=0.0)
    bt = Backtester(_AlwaysBuy(), RiskManager(limits), starting_cash=10_000, slippage_bps=0)
    result = bt.run("X", _rising_series())
    assert "take-profit" in [t["reason"] for t in result.trades]


def test_no_take_profit_when_disabled():
    limits = RiskLimits(max_position_pct=1.0, stop_loss_pct=0.5,
                        take_profit_pct=0.0, min_cash_pct=0.0)
    bt = Backtester(_AlwaysBuy(), RiskManager(limits), starting_cash=10_000, slippage_bps=0)
    result = bt.run("X", _rising_series())
    assert "take-profit" not in [t["reason"] for t in result.trades]


def test_risk_tier_switches_limits_by_equity():
    base = RiskLimits.aggressive()          # 60% below the tier
    tier = RiskTier(min_equity=100, limits=RiskLimits(max_position_pct=0.15))
    rm = RiskManager(base, tiers=[tier])

    rm.observe_equity(15)                   # tiny balance -> aggressive
    assert rm.limits.max_position_pct == 0.60
    rm.observe_equity(100)                  # crossed $100 -> de-risked to 15%
    assert rm.limits.max_position_pct == 0.15
    rm.observe_equity(50)                   # dropped back below -> aggressive again
    assert rm.limits.max_position_pct == 0.60


def test_no_tiers_keeps_base_limits():
    rm = RiskManager(RiskLimits.aggressive())
    rm.observe_equity(1_000_000)
    assert rm.limits.max_position_pct == 0.60
