"""In-memory paper broker: fills market orders instantly at a fed price.

Used by the backtester and by live *dry-run* mode. Models commission and a
simple slippage bps so paper results aren't unrealistically clean.
"""

from __future__ import annotations

from datetime import datetime

from ..core.models import (
    AccountState,
    Order,
    OrderStatus,
    Position,
    Side,
)
from .base import Broker


class PaperBroker(Broker):
    name = "paper"
    is_live = False
    # The paper broker fully models signed (long/short) positions, so dry-run
    # of a long/short equity strategy behaves like the real Alpaca margin path.
    supports_short = True

    def __init__(self, starting_cash: float = 10_000.0,
                 commission: float = 0.0, slippage_bps: float = 1.0):
        self.cash = starting_cash
        self.commission = commission
        self.slippage_bps = slippage_bps
        self._positions: dict[str, Position] = {}
        self._prices: dict[str, float] = {}
        self._order_seq = 0

    # The backtester/engine feeds the current price before asking for fills.
    def set_price(self, symbol: str, price: float) -> None:
        self._prices[symbol] = price

    def last_price(self, symbol: str) -> float:
        return self._prices.get(symbol, 0.0)

    def positions(self) -> dict[str, Position]:
        return self._positions

    def account(self) -> AccountState:
        equity = self.cash + sum(
            p.quantity * self._prices.get(s, p.avg_price) for s, p in self._positions.items()
        )
        gross = sum(abs(p.quantity) * self._prices.get(s, p.avg_price)
                    for s, p in self._positions.items())
        return AccountState(cash=self.cash, equity=equity, gross_value=gross,
                            positions=dict(self._positions), timestamp=datetime.now())

    def _fill_price(self, side: Side, ref: float) -> float:
        adj = ref * self.slippage_bps / 10_000
        return ref + adj if side is Side.BUY else ref - adj

    def submit(self, order: Order) -> Order:
        """Signed-position accounting: BUY adds +qty, SELL adds -qty. A SELL from
        flat (or covering past zero) opens a SHORT; a BUY covers a short. Cash
        moves by -delta*price, so shorting adds cash and covering spends it."""
        ref = self._prices.get(order.symbol)
        if not ref or ref <= 0:
            order.status = OrderStatus.REJECTED
            return order

        price = self._fill_price(order.side, ref)
        pos = self._positions.get(order.symbol, Position(order.symbol))
        old_qty = pos.quantity
        delta = order.quantity if order.side is Side.BUY else -order.quantity

        # Only a cash-spending BUY can be rejected for insufficient cash.
        if order.side is Side.BUY and delta * price + self.commission > self.cash:
            order.status = OrderStatus.REJECTED
            return order

        self.cash -= delta * price + self.commission
        new_qty = old_qty + delta

        if old_qty == 0 or (old_qty > 0) == (delta > 0):
            # Opening or adding in the same direction -> weighted-average entry.
            denom = abs(old_qty) + abs(delta)
            pos.avg_price = ((pos.avg_price * abs(old_qty) + price * abs(delta)) / denom
                             if denom else 0.0)
        elif abs(delta) > abs(old_qty):
            pos.avg_price = price  # reversed through zero -> new entry is the fill
        # else: partial reduce -> keep the existing average

        pos.quantity = new_qty
        if abs(pos.quantity) < 1e-9:
            self._positions.pop(order.symbol, None)
        else:
            self._positions[order.symbol] = pos

        self._order_seq += 1
        order.broker_id = f"paper-{self._order_seq}"
        order.status = OrderStatus.FILLED
        order.filled_price = price
        order.filled_quantity = order.quantity
        return order

    def cancel(self, broker_id: str) -> None:  # instant fills => nothing to cancel
        return None
