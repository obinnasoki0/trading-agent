"""Notional (dollar-amount) orders. On a broker that supports it (Robinhood),
a BUY is placed as a dollar amount ("$X worth") rather than a fractional share
count, so a small account can trade. Sells still go by share quantity so a
position closes exactly."""

import pandas as pd

from trading_agent.brokers.paper import PaperBroker
from trading_agent.brokers.robinhood_mcp import RobinhoodMCPBroker
from trading_agent.core.engine import TradingEngine
from trading_agent.core.models import AccountState, Order, OrderStatus, OrderType, Side, Signal
from trading_agent.core.risk import RiskLimits, RiskManager
from trading_agent.strategies.base import Strategy


# -- broker payload: dollar_amount for buys, quantity for sells ---------------
def _capturing_rh():
    b = RobinhoodMCPBroker(allow_live=True, dry_run=False)
    captured = {}
    b._resolve_account_number = lambda: "ACC"
    b._call = lambda op, payload: captured.update(payload) or {"id": "o1"}
    return b, captured


def test_robinhood_buy_uses_dollar_amount():
    b, captured = _capturing_rh()
    b.submit(Order("AAPL", Side.BUY, 0.5, OrderType.MARKET, dollar_amount=25.0))
    assert captured.get("dollar_amount") == "25.00"
    assert "quantity" not in captured


def test_robinhood_sell_uses_share_quantity():
    b, captured = _capturing_rh()
    # Even with a dollar_amount present, a SELL closes by exact shares.
    b.submit(Order("AAPL", Side.SELL, 0.5, OrderType.MARKET, dollar_amount=25.0))
    assert captured.get("quantity") == "0.5"
    assert "dollar_amount" not in captured


def test_robinhood_buy_without_dollar_amount_uses_quantity():
    b, captured = _capturing_rh()
    b.submit(Order("AAPL", Side.BUY, 0.5, OrderType.MARKET))
    assert captured.get("quantity") == "0.5"
    assert "dollar_amount" not in captured


def test_robinhood_declares_notional_support():
    assert RobinhoodMCPBroker.supports_notional is True
    assert PaperBroker.supports_notional is False


# -- engine attaches dollar_amount on a notional-capable broker ---------------
class _CapturingBroker:
    name = "cap"
    is_live = False
    supports_notional = True
    supports_short = False

    def __init__(self, cash=100.0):
        self.cash = cash
        self.submitted = []
        self._pos = {}

    def account(self):
        return AccountState(cash=self.cash, equity=self.cash, positions=dict(self._pos))

    def positions(self):
        return self._pos

    def last_price(self, symbol):
        return 50.0

    def submit(self, order):
        self.submitted.append(order)
        order.status = OrderStatus.FILLED
        order.filled_price = 50.0
        order.filled_quantity = order.quantity
        order.broker_id = "x"
        return order


class _Bull(Strategy):
    name = "bull"
    warmup = 1

    def generate(self, symbol, history):
        return Signal(symbol, 0.9, history.index[-1], "t")


class _Data:
    def history(self, symbol, start, end):
        idx = pd.date_range("2022-01-01", periods=3, freq="B")
        return pd.DataFrame({"open": 50.0, "high": 50.0, "low": 50.0,
                             "close": 50.0, "volume": 1e6}, index=idx)


def test_engine_attaches_dollar_amount_for_notional_broker():
    broker = _CapturingBroker(cash=100.0)
    rm = RiskManager(RiskLimits(max_position_pct=0.5, min_cash_pct=0.0,
                                risk_per_trade_pct=0.5, stop_loss_pct=0.5))
    eng = TradingEngine(broker, _Bull(), rm, _Data(), symbols=["AAA"], lookback_days=10)
    eng.step()
    assert broker.submitted, "expected a buy"
    order = broker.submitted[0]
    assert order.side is Side.BUY
    assert order.dollar_amount is not None
    # $50 cap (50% of $100) at $50/share = 1 share = $50 notional.
    assert abs(order.dollar_amount - 50.0) < 0.01
