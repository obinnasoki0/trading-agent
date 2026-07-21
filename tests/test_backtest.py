from datetime import datetime

from trading_agent.core.backtest import Backtester
from trading_agent.core.data import SyntheticData
from trading_agent.core.risk import RiskLimits, RiskManager
from trading_agent.strategies.sma_crossover import SmaCrossover


def test_backtest_runs_and_reports():
    data = SyntheticData().history("TEST", datetime(2022, 1, 1), datetime(2024, 1, 1))
    bt = Backtester(SmaCrossover(fast=10, slow=30), RiskManager(RiskLimits()), starting_cash=10_000)
    result = bt.run("TEST", data)
    summary = result.summary()
    assert summary["final_equity"] > 0
    assert len(result.equity_curve) == len(data)
    assert "max_drawdown" in summary


def test_backtest_never_exceeds_position_cap():
    data = SyntheticData().history("TEST", datetime(2022, 1, 1), datetime(2024, 1, 1))
    limits = RiskLimits(max_position_pct=0.10)
    bt = Backtester(SmaCrossover(fast=5, slow=15), RiskManager(limits), starting_cash=10_000)
    bt.run("TEST", data)
    # After the run, any held position must be within the cap of current equity.
    acct = bt.broker.account()
    for sym, pos in acct.positions.items():
        val = pos.quantity * bt.broker.last_price(sym)
        assert val <= 0.11 * acct.equity  # small tolerance for slippage/marks
