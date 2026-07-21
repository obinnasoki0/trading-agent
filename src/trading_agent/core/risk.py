"""The risk manager -- the most important file in this project.

Returns are protected by *not losing*, not by chasing upside. Every order the
agent wants to place is filtered through here. It can shrink an order, veto it,
or trigger a full liquidation when a drawdown/loss limit is breached.

All limits are hard and configured up front. A strategy cannot override them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import AccountState, Order, Side


@dataclass
class RiskLimits:
    # Fraction of equity allowed in a single position (0.10 = 10%).
    max_position_pct: float = 0.10
    # Fraction of equity to risk per trade, used for stop-based sizing.
    risk_per_trade_pct: float = 0.01
    # Stop-loss distance from entry (0.05 = exit if 5% underwater).
    stop_loss_pct: float = 0.05
    # Halt all new buying for the day once equity is down this much vs. day open.
    max_daily_loss_pct: float = 0.03
    # Liquidate everything if equity falls this far below its running peak.
    max_drawdown_pct: float = 0.20
    # Never deploy more than this fraction of equity across all positions.
    max_gross_exposure_pct: float = 1.0
    # Refuse to trade if cash would drop below this fraction of equity.
    min_cash_pct: float = 0.0


class RiskDecision:
    def __init__(self, approved: bool, order: Order | None, reason: str):
        self.approved = approved
        self.order = order
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"RiskDecision(approved={self.approved}, reason={self.reason!r})"


class RiskManager:
    def __init__(self, limits: RiskLimits | None = None):
        self.limits = limits or RiskLimits()
        self._peak_equity: float | None = None
        self._day_open_equity: float | None = None
        self.halted = False

    # -- daily / drawdown bookkeeping ------------------------------------
    def start_day(self, equity: float) -> None:
        self._day_open_equity = equity
        self.halted = False

    def observe_equity(self, equity: float) -> None:
        self._peak_equity = equity if self._peak_equity is None else max(self._peak_equity, equity)
        if self._day_open_equity is None:
            self._day_open_equity = equity

    def kill_switch_triggered(self, equity: float) -> str | None:
        """Return a reason string if everything must be liquidated, else None."""
        self.observe_equity(equity)
        if self._peak_equity and self._peak_equity > 0:
            drawdown = 1 - equity / self._peak_equity
            if drawdown >= self.limits.max_drawdown_pct:
                return f"max drawdown breached: {drawdown:.1%} >= {self.limits.max_drawdown_pct:.1%}"
        if self._day_open_equity and self._day_open_equity > 0:
            daily = 1 - equity / self._day_open_equity
            if daily >= self.limits.max_daily_loss_pct:
                self.halted = True  # stop new buys for the rest of the day
        return None

    # -- position sizing --------------------------------------------------
    def size_for(self, symbol: str, price: float, equity: float) -> float:
        """Shares to buy, using the smaller of position cap and stop-risk sizing."""
        if price <= 0 or equity <= 0:
            return 0.0
        cap_shares = (self.limits.max_position_pct * equity) / price
        stop_dist = self.limits.stop_loss_pct * price
        risk_shares = (self.limits.risk_per_trade_pct * equity) / stop_dist if stop_dist > 0 else cap_shares
        return float(max(0.0, min(cap_shares, risk_shares)))

    # -- the gate ---------------------------------------------------------
    def review(self, order: Order, price: float, account: AccountState) -> RiskDecision:
        equity = account.equity
        self.observe_equity(equity)

        if order.side is Side.SELL:
            return RiskDecision(True, order, "sell/exit always allowed")

        if self.halted:
            return RiskDecision(False, None, "daily loss limit hit; new buys halted")

        gross = sum(abs(p.quantity) * price for p in account.positions.values())
        if gross + order.quantity * price > self.limits.max_gross_exposure_pct * equity:
            return RiskDecision(False, None, "gross exposure cap reached")

        existing = account.positions.get(order.symbol)
        existing_val = (existing.quantity * price) if existing else 0.0
        new_val = existing_val + order.quantity * price
        if new_val > self.limits.max_position_pct * equity:
            allowed_val = self.limits.max_position_pct * equity - existing_val
            allowed_qty = max(0.0, allowed_val / price)
            if allowed_qty <= 0:
                return RiskDecision(False, None, "position size cap reached for symbol")
            order.quantity = allowed_qty

        cost = order.quantity * price
        if account.cash - cost < self.limits.min_cash_pct * equity:
            return RiskDecision(False, None, "insufficient cash under min-cash buffer")

        if order.quantity <= 0:
            return RiskDecision(False, None, "sized to zero shares")

        return RiskDecision(True, order, "approved")
