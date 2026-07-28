"""Let winners run: with let_winners_run on, the fixed take-profit is replaced by
a trailing stop, so a position rides a trend and only exits on a pullback from
its peak (or on an analysis reversal, tested elsewhere)."""

import pandas as pd

from trading_agent.brokers.paper import PaperBroker
from trading_agent.core.engine import TradingEngine
from trading_agent.core.models import Signal
from trading_agent.core.risk import RiskLimits, RiskManager
from trading_agent.strategies.base import Strategy


class _FixedSignal(Strategy):
    name = "fixed"
    warmup = 1

    def __init__(self, strength):
        self.strength = strength

    def generate(self, symbol, history):
        return Signal(symbol, self.strength, history.index[-1], "test")


class _Data:
    def __init__(self, price):
        self.price = price

    def history(self, symbol, start, end):
        idx = pd.date_range("2022-01-01", periods=3, freq="B")
        p = self.price
        return pd.DataFrame({"open": p, "high": p, "low": p, "close": p, "volume": 1e6}, index=idx)


def _engine(let_winners_run, price=100.0, strength=0.9):
    broker = PaperBroker(starting_cash=10_000, slippage_bps=0)
    rm = RiskManager(RiskLimits(max_position_pct=1.0, min_cash_pct=0.0,
                                risk_per_trade_pct=0.01, stop_loss_pct=0.10,
                                take_profit_pct=0.08))
    eng = TradingEngine(broker, _FixedSignal(strength), rm, _Data(price),
                        symbols=["AAA"], lookback_days=10, let_winners_run=let_winners_run)
    return eng, broker


def test_fixed_mode_takes_profit_at_cap():
    eng, broker = _engine(let_winners_run=False, price=100.0)
    eng.step()                                    # open at 100
    assert broker.positions()["AAA"].quantity > 0
    eng.data = _Data(109.0)                        # +9% > 8% take-profit
    eng.step()
    assert "AAA" not in broker.positions()         # fixed take-profit closed it


def test_let_winners_run_rides_past_the_cap():
    eng, broker = _engine(let_winners_run=True, price=100.0)
    eng.step()                                    # open at 100
    eng.data = _Data(109.0)                        # +9% -> fixed mode would sell
    eng.step()
    assert broker.positions().get("AAA") is not None  # still held -> winner runs
    eng.data = _Data(140.0)                        # keeps climbing
    eng.step()
    assert broker.positions().get("AAA") is not None
    assert eng._peak_price["AAA"] == 140.0         # peak tracks the high-water mark


def test_let_winners_run_exits_on_pullback_from_peak():
    eng, broker = _engine(let_winners_run=True, price=100.0)
    eng.step()
    eng.data = _Data(140.0)                        # run up; peak = 140
    eng.step()
    assert broker.positions().get("AAA") is not None
    eng.data = _Data(125.0)                        # -10.7% off the 140 peak (> 10% stop)
    eng.step()
    assert "AAA" not in broker.positions()         # trailing stop banked the gain


def test_let_winners_run_still_stops_a_loser():
    eng, broker = _engine(let_winners_run=True, price=100.0)
    eng.step()                                    # open at 100, peak = 100
    eng.data = _Data(89.0)                          # -11% with no prior gain (> 10% stop)
    eng.step()
    assert "AAA" not in broker.positions()         # trailing stop from entry still protects
