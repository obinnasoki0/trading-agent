"""Gross-exposure must be computed from position market value (equity - cash),
not quantity * the order's price -- otherwise a high-quantity holding (e.g.
millions of a cheap crypto) in a shared account vetoes every unrelated buy."""

from trading_agent.core.models import AccountState, Order, Position, Side
from trading_agent.core.risk import RiskLimits, RiskManager


def test_high_quantity_holding_does_not_falsely_block_buys():
    rm = RiskManager(RiskLimits(max_position_pct=0.20, max_gross_exposure_pct=0.90,
                                min_cash_pct=0.0))
    # Account holds 5,000,000 of a cheap token worth $5,000 total; $95k cash.
    positions = {"SHIB/USD": Position("SHIB/USD", quantity=5_000_000, avg_price=0.001)}
    account = AccountState(cash=95_000, equity=100_000, positions=positions)

    # Buying a $150 stock must be allowed -- real gross is only 5% ($5k of $100k).
    order = Order("AAPL", Side.BUY, quantity=100)   # $15k
    decision = rm.review(order, price=150.0, account=account)
    assert decision.approved, decision.reason


def test_gross_cap_still_blocks_when_genuinely_over():
    rm = RiskManager(RiskLimits(max_position_pct=1.0, max_gross_exposure_pct=0.90,
                                min_cash_pct=0.0))
    # 92% already deployed (equity 100k, cash 8k) -> a new buy exceeds the 90% cap.
    account = AccountState(cash=8_000, equity=100_000,
                           positions={"X": Position("X", quantity=920, avg_price=100.0)})
    decision = rm.review(Order("Y", Side.BUY, quantity=50), price=100.0, account=account)
    assert not decision.approved
    assert "gross" in decision.reason
