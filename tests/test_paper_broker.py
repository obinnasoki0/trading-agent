from trading_agent.brokers.paper import PaperBroker
from trading_agent.core.models import Order, Side


def test_buy_then_sell_round_trip():
    b = PaperBroker(starting_cash=1_000, commission=0.0, slippage_bps=0.0)
    b.set_price("AAPL", 100.0)
    b.submit(Order("AAPL", Side.BUY, 5))
    assert b.positions()["AAPL"].quantity == 5
    assert b.cash == 500.0

    b.set_price("AAPL", 110.0)
    b.submit(Order("AAPL", Side.SELL, 5))
    assert "AAPL" not in b.positions()
    assert b.cash == 1_050.0  # +$50 profit


def test_buy_rejected_when_insufficient_cash():
    b = PaperBroker(starting_cash=100)
    b.set_price("AAPL", 100.0)
    order = b.submit(Order("AAPL", Side.BUY, 5))  # needs $500
    assert order.status.value == "rejected"
    assert not b.positions()
