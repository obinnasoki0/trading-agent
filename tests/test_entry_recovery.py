"""After a restart the in-memory entry price is gone; the engine must recover it
from the broker's reported average so stops/take-profits still protect positions
opened before this process started."""

from trading_agent.core.engine import TradingEngine
from trading_agent.core.models import AccountState, OrderStatus, Position
from trading_agent.core.risk import RiskLimits, RiskManager


class _HoldingBroker:
    is_live = False

    def __init__(self):
        self._pos = {"AAA": Position("AAA", quantity=10, avg_price=100.0)}
        self.sold = []

    def positions(self):
        return self._pos

    def last_price(self, _s):
        return 100.0

    def submit(self, order):
        order.status = OrderStatus.FILLED
        order.broker_id = "x"
        order.filled_price = 94.0
        order.filled_quantity = order.quantity
        self.sold.append(order.symbol)
        self._pos.pop(order.symbol, None)
        return order

    def account(self):
        return AccountState(cash=0.0, equity=1000.0, positions=dict(self._pos))


def _engine(broker):
    rm = RiskManager(RiskLimits(stop_loss_pct=0.05, take_profit_pct=0.0))
    return TradingEngine(broker, strategy=None, risk=rm, data=None, symbols=["AAA"])


def test_stop_fires_on_recovered_entry_after_restart():
    broker = _HoldingBroker()
    engine = _engine(broker)                 # fresh engine => empty _entry_price
    # Price 94 is below the 5% stop off the broker's avg (100 * 0.95 = 95).
    sold = engine._handle_exit("AAA", 94.0, actions=[])
    assert sold is True
    assert broker.sold == ["AAA"]
    assert engine._entry_price.get("AAA") == 100.0   # recovered from the broker


def test_no_exit_when_position_healthy():
    broker = _HoldingBroker()
    engine = _engine(broker)
    assert engine._handle_exit("AAA", 99.0, actions=[]) is False   # only 1% down
    assert broker.sold == []
