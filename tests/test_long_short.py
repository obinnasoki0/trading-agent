"""Long/short behavior.

The agent goes long on bullish setups and short on bearish ones (only when
shorting is enabled AND the broker supports it -- Alpaca equities / paper).
These guard the signed-position accounting, the direction-aware stops, and the
gating that keeps crypto/Robinhood long-only.
"""

import pandas as pd

from trading_agent.brokers.paper import PaperBroker
from trading_agent.core.engine import TradingEngine
from trading_agent.core.models import Order, Side, Signal
from trading_agent.core.risk import RiskLimits, RiskManager
from trading_agent.strategies.base import Strategy


# -- paper broker signed positions -------------------------------------------
def test_paper_short_open_adds_cash_and_negative_qty():
    b = PaperBroker(starting_cash=10_000, slippage_bps=0)
    b.set_price("AAPL", 100.0)
    b.submit(Order("AAPL", Side.SELL, 10))  # short 10 from flat
    pos = b.positions()["AAPL"]
    assert pos.quantity == -10
    assert pos.avg_price == 100.0
    assert b.cash == 11_000.0  # shorting brings in cash


def test_paper_cover_short_removes_position():
    b = PaperBroker(starting_cash=10_000, slippage_bps=0)
    b.set_price("AAPL", 100.0)
    b.submit(Order("AAPL", Side.SELL, 10))
    b.set_price("AAPL", 90.0)
    b.submit(Order("AAPL", Side.BUY, 10))   # cover at a profit
    assert "AAPL" not in b.positions()
    assert b.cash == 10_100.0  # 10k +1k short -900 cover = +100 profit


def test_paper_gross_value_counts_shorts_absolutely():
    b = PaperBroker(starting_cash=10_000, slippage_bps=0)
    b.set_price("AAPL", 100.0)
    b.submit(Order("AAPL", Side.SELL, 10))  # short worth |1000|
    assert b.account().gross_value == 1000.0


# -- engine direction logic ---------------------------------------------------
class _FixedSignal(Strategy):
    name = "fixed"
    warmup = 1

    def __init__(self, strength):
        self.strength = strength

    def generate(self, symbol, history):
        return Signal(symbol, self.strength, history.index[-1], "test")


class _FlatData:
    def __init__(self, price=100.0):
        self.price = price

    def history(self, symbol, start, end):
        idx = pd.date_range("2022-01-01", periods=3, freq="B")
        return pd.DataFrame({"open": self.price, "high": self.price, "low": self.price,
                             "close": self.price, "volume": 1e6}, index=idx)


def _engine(strength, allow_short=True, price=100.0):
    broker = PaperBroker(starting_cash=10_000, slippage_bps=0)
    rm = RiskManager(RiskLimits(max_position_pct=0.10, min_cash_pct=0.0, stop_loss_pct=0.05))
    eng = TradingEngine(broker, _FixedSignal(strength), rm, _FlatData(price),
                        symbols=["AAPL"], lookback_days=10, allow_short=allow_short,
                        short_size_mult=0.5)
    return eng, broker


def test_bearish_signal_opens_short_when_enabled():
    eng, broker = _engine(strength=-0.8, allow_short=True)
    eng.step()
    pos = broker.positions().get("AAPL")
    assert pos is not None and pos.quantity < 0


def test_bearish_signal_stays_flat_when_short_disabled():
    eng, broker = _engine(strength=-0.8, allow_short=False)
    eng.step()
    assert "AAPL" not in broker.positions()


def test_short_sized_smaller_than_long():
    long_eng, long_b = _engine(strength=0.8, allow_short=True)
    long_eng.step()
    short_eng, short_b = _engine(strength=-0.8, allow_short=True)
    short_eng.step()
    long_qty = abs(long_b.positions()["AAPL"].quantity)
    short_qty = abs(short_b.positions()["AAPL"].quantity)
    assert short_qty == long_qty * 0.5


def test_short_stop_loss_fires_when_price_rises():
    eng, broker = _engine(strength=-0.8, allow_short=True, price=100.0)
    eng.step()  # opens a short at 100
    assert broker.positions()["AAPL"].quantity < 0
    # Price rises 6% (past the 5% stop) -> short should be covered.
    eng.data = _FlatData(price=106.0)
    eng.step()
    assert "AAPL" not in broker.positions()


def test_short_covers_when_signal_turns_bullish():
    eng, broker = _engine(strength=-0.8, allow_short=True)
    eng.step()
    assert broker.positions()["AAPL"].quantity < 0
    eng.strategy = _FixedSignal(0.8)  # flip bullish
    eng.step()
    assert "AAPL" not in broker.positions()


def test_engine_wont_short_on_long_only_broker():
    # A broker that doesn't support shorting keeps the agent flat on bearish
    # signals even if allow_short is on (crypto / Robinhood path).
    broker = PaperBroker(starting_cash=10_000, slippage_bps=0)
    broker.supports_short = False
    rm = RiskManager(RiskLimits(max_position_pct=0.10, min_cash_pct=0.0))
    eng = TradingEngine(broker, _FixedSignal(-0.8), rm, _FlatData(), symbols=["AAPL"],
                        lookback_days=10, allow_short=True)
    eng.step()
    assert "AAPL" not in broker.positions()
