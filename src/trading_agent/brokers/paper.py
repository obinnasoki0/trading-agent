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
        return AccountState(cash=self.cash, equity=equity,
                            positions=dict(self._positions), timestamp=datetime.now())

    def _fill_price(self, side: Side, ref: float) -> float:
        adj = ref * self.slippage_bps / 10_000
        return ref + adj if side is Side.BUY else ref - adj

    def submit(self, order: Order) -> Order:
        ref = self._prices.get(order.symbol)
        if not ref or ref <= 0:
            order.status = OrderStatus.REJECTED
            return order

        price = self._fill_price(order.side, ref)
        pos = self._positions.get(order.symbol, Position(order.symbol))

        if order.side is Side.BUY:
            cost = price * order.quantity + self.commission
            if cost > self.cash:
                order.status = OrderStatus.REJECTED
                return order
            self.cash -= cost
            new_qty = pos.quantity + order.quantity
            pos.avg_price = ((pos.avg_price * pos.quantity) + price * order.quantity) / new_qty if new_qty else 0.0
            pos.quantity = new_qty
        else:  # SELL
            qty = min(order.quantity, pos.quantity)
            if qty <= 0:
                order.status = OrderStatus.REJECTED
                return order
            self.cash += price * qty - self.commission
            pos.quantity -= qty
            order.quantity = qty

        if pos.quantity <= 1e-9:
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
