from datetime import datetime

from trading_agent.core.backtest import PortfolioBacktester
from trading_agent.core.data import SyntheticData
from trading_agent.core.risk import RiskLimits, RiskManager
from trading_agent.strategies.momentum import Momentum


def _data(symbols):
    prov = SyntheticData()
    return {s: prov.history(s, datetime(2022, 1, 1), datetime(2024, 1, 1)) for s in symbols}


def test_portfolio_runs_over_multiple_symbols():
    bt = PortfolioBacktester(Momentum(lookback=60), RiskManager(RiskLimits.medium()),
                             starting_cash=10_000)
    result = bt.run(_data(["AAA", "BBB", "CCC"]))
    assert result.summary()["final_equity"] > 0
    assert len(result.equity_curve) > 0


def test_shared_account_respects_gross_exposure_and_cash():
    # With many symbols and a tight gross cap, the shared account must never
    # over-deploy: cash stays non-negative and gross exposure honors the cap.
    limits = RiskLimits(max_position_pct=0.10, max_gross_exposure_pct=0.5, min_cash_pct=0.1)
    bt = PortfolioBacktester(Momentum(lookback=40), RiskManager(limits), starting_cash=10_000)
    bt.run(_data(["AAA", "BBB", "CCC", "DDD", "EEE"]))
    acct = bt.broker.account()
    assert bt.broker.cash >= 0
    gross = sum(p.quantity * bt.broker.last_price(s) for s, p in acct.positions.items())
    assert gross <= 0.6 * acct.equity  # cap + small mark-to-market tolerance


def test_empty_input_raises():
    bt = PortfolioBacktester(Momentum(), RiskManager(RiskLimits.medium()))
    try:
        bt.run({})
        assert False, "expected ValueError"
    except ValueError:
        pass
