from trading_agent.core.models import AccountState, Order, Position, Side
from trading_agent.core.risk import RiskLimits, RiskManager


def _account(cash, equity, positions=None):
    return AccountState(cash=cash, equity=equity, positions=positions or {})


def test_position_cap_shrinks_order():
    rm = RiskManager(RiskLimits(max_position_pct=0.10, min_cash_pct=0.0))
    acct = _account(cash=10_000, equity=10_000)
    order = Order("AAPL", Side.BUY, quantity=100)  # 100 * $100 = $10k = 100% equity
    decision = rm.review(order, price=100.0, account=acct)
    assert decision.approved
    # Capped to 10% of 10k / $100 = 10 shares.
    assert abs(decision.order.quantity - 10) < 1e-6


def test_sell_always_allowed_even_when_halted():
    rm = RiskManager()
    rm.halted = True
    acct = _account(cash=0, equity=5_000, positions={"AAPL": Position("AAPL", 10, 100)})
    decision = rm.review(Order("AAPL", Side.SELL, 10), price=90.0, account=acct)
    assert decision.approved


def test_daily_loss_halts_new_buys():
    rm = RiskManager(RiskLimits(max_daily_loss_pct=0.03))
    rm.start_day(10_000)
    rm.kill_switch_triggered(9_600)  # down 4% on the day
    assert rm.halted
    decision = rm.review(Order("AAPL", Side.BUY, 1), price=100.0, account=_account(9_600, 9_600))
    assert not decision.approved


def test_drawdown_kill_switch():
    rm = RiskManager(RiskLimits(max_drawdown_pct=0.20))
    rm.observe_equity(10_000)
    rm.observe_equity(12_000)  # new peak
    assert rm.kill_switch_triggered(9_500) is not None  # >20% off peak of 12k


def test_size_for_respects_stop_risk():
    rm = RiskManager(RiskLimits(max_position_pct=1.0, risk_per_trade_pct=0.01, stop_loss_pct=0.05))
    # risk sizing: 0.01*10000 / (0.05*100) = 100/5 = 20 shares
    assert abs(rm.size_for("X", price=100.0, equity=10_000) - 20) < 1e-6
