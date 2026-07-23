"""The engine must label orders honestly -- a live broker held in dry-run must
never print [LIVE]. This guards the exact confusion seen in the first live-looking
dry-run output."""

from trading_agent.core.engine import TradingEngine
from trading_agent.core.models import Order, OrderStatus, Side
from trading_agent.core.risk import RiskLimits, RiskManager


class _Broker:
    def __init__(self, is_live, result):
        self.is_live = is_live
        self._result = result

    def submit(self, order):
        order.status, order.broker_id, order.filled_price, order.filled_quantity = self._result
        return order

    def last_price(self, _s):
        return 100.0


def _engine(broker):
    return TradingEngine(broker, strategy=None, risk=RiskManager(RiskLimits()),
                         data=None, symbols=[])


def test_live_broker_in_dry_run_tags_dry_run():
    broker = _Broker(True, (OrderStatus.REJECTED, "dry-run", None, 0.0))
    actions = []
    _engine(broker)._submit_order(Order("AAPL", Side.BUY, 0.0023), 201.3, actions, "sig")
    assert actions[0].startswith("[DRY-RUN] would buy")
    assert "[LIVE]" not in actions[0]


def test_live_broker_real_fill_tags_live():
    broker = _Broker(True, (OrderStatus.FILLED, "rh-1", 201.3, 0.0023))
    actions = []
    _engine(broker)._submit_order(Order("AAPL", Side.BUY, 0.0023), 201.3, actions, "sig")
    assert actions[0].startswith("[LIVE] buy")


def test_paper_broker_tags_paper():
    broker = _Broker(False, (OrderStatus.FILLED, "paper-1", 201.3, 0.0023))
    actions = []
    _engine(broker)._submit_order(Order("AAPL", Side.BUY, 0.0023), 201.3, actions, "sig")
    assert actions[0].startswith("[PAPER] buy")
