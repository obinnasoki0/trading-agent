"""Daily profit target (the 'bank and keep hunting' ratchet).

Each time equity climbs another daily_profit_target_pct above the last
checkpoint, the agent banks its winners (realizes the gains) and keeps trading;
just-banked names sit out a short cooldown so it rotates into fresh signals
instead of instantly rebuying what it sold.
"""

import pandas as pd

from trading_agent.brokers.paper import PaperBroker
from trading_agent.core.engine import TradingEngine
from trading_agent.core.models import Signal
from trading_agent.core.risk import RiskLimits, RiskManager
from trading_agent.strategies.base import Strategy


# -- the ratchet itself -------------------------------------------------------
def test_harvest_due_ratchets_each_increment():
    rm = RiskManager(RiskLimits(daily_profit_target_pct=0.02))
    rm.start_day(10_000)
    assert rm.harvest_due(10_000) is False       # flat -> not due
    assert rm.harvest_due(10_200) is True        # +2% -> bank
    assert rm.harvest_due(10_200) is False       # checkpoint advanced, not again
    assert rm.harvest_due(10_404) is True        # another +2% from 10_200


def test_harvest_off_when_target_zero():
    rm = RiskManager(RiskLimits(daily_profit_target_pct=0.0))
    rm.start_day(10_000)
    assert rm.harvest_due(20_000) is False       # disabled regardless of gain


def test_start_day_resets_checkpoint():
    rm = RiskManager(RiskLimits(daily_profit_target_pct=0.02))
    rm.start_day(10_000)
    assert rm.harvest_due(10_200) is True
    rm.start_day(10_200)                          # new day
    assert rm.harvest_due(10_200) is False        # fresh checkpoint, not immediately due


# -- engine: bank winners, then keep hunting ----------------------------------
class _Bull(Strategy):
    name = "bull"
    warmup = 1

    def __init__(self, bullish):
        self.bullish = bullish

    def generate(self, symbol, history):
        return Signal(symbol, 0.9 if symbol in self.bullish else 0.0, history.index[-1], "test")


class _Data:
    def __init__(self, prices):
        self.prices = prices

    def history(self, symbol, start, end):
        idx = pd.date_range("2022-01-01", periods=3, freq="B")
        p = self.prices[symbol]
        return pd.DataFrame({"open": p, "high": p, "low": p, "close": p, "volume": 1e6}, index=idx)


def _engine(bullish, prices, target=0.02, cooldown=3):
    broker = PaperBroker(starting_cash=10_000, slippage_bps=0)
    rm = RiskManager(RiskLimits(max_position_pct=1.0, min_cash_pct=0.0,
                                risk_per_trade_pct=0.01, stop_loss_pct=0.05,
                                take_profit_pct=0.0, daily_profit_target_pct=target))
    eng = TradingEngine(broker, _Bull(bullish), rm, _Data(prices),
                        symbols=list(prices), lookback_days=10,
                        profit_bank_cooldown_cycles=cooldown)
    return eng, broker


def test_banks_winner_and_keeps_hunting_with_cooldown():
    eng, broker = _engine(bullish={"AAA"}, prices={"AAA": 100.0, "BBB": 100.0})
    eng.step()                                    # opens AAA
    assert broker.positions()["AAA"].quantity > 0

    # AAA rips +30% (moves total equity past the +2% target); BBB now qualifies.
    eng.data = _Data({"AAA": 130.0, "BBB": 100.0})
    eng.strategy = _Bull({"AAA", "BBB"})
    eng.step()                                    # ratchet: bank AAA, open BBB
    assert "AAA" not in broker.positions()        # banked the winner
    assert broker.positions().get("BBB") is not None  # kept hunting

    # Next cycle AAA is still bullish but on cooldown -> not rebought.
    eng.data = _Data({"AAA": 130.0, "BBB": 100.0})
    eng.strategy = _Bull({"AAA", "BBB"})
    eng.step()
    assert "AAA" not in broker.positions()        # cooldown blocked the rebuy


def test_no_harvest_when_nothing_in_profit():
    eng, broker = _engine(bullish={"AAA"}, prices={"AAA": 100.0}, target=0.001)
    eng.step()                                    # opens AAA at 100
    # Force the ratchet to fire, but AAA is flat (no profit) -> nothing banked.
    eng.risk._profit_checkpoint = 1.0             # any tiny equity beats 1.0*(1+t)
    actions = eng.step()
    assert broker.positions().get("AAA") is not None  # still held (not in profit)
    assert any("no positions in profit" in a for a in actions)
