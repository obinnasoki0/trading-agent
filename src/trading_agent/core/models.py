"""Core value objects shared across the agent.

These are deliberately plain dataclasses so they serialize cleanly and are
easy to reason about in tests and backtests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Bar:
    """A single OHLCV candle for one symbol."""

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Signal:
    """A strategy's intent for a symbol on a given bar.

    ``strength`` is a normalized conviction in [-1, 1]: +1 = strong long,
    -1 = strong short/exit, 0 = no opinion. The risk manager, not the
    strategy, decides the actual size.
    """

    symbol: str
    strength: float
    timestamp: datetime
    reason: str = ""

    def __post_init__(self) -> None:
        self.strength = max(-1.0, min(1.0, float(self.strength)))


@dataclass
class Order:
    symbol: str
    side: Side
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    status: OrderStatus = OrderStatus.PENDING
    filled_price: float | None = None
    filled_quantity: float = 0.0
    broker_id: str | None = None
    created_at: datetime | None = None

    @property
    def notional(self) -> float:
        price = self.filled_price if self.filled_price is not None else (self.limit_price or 0.0)
        qty = self.filled_quantity or self.quantity
        return price * qty


@dataclass
class Position:
    symbol: str
    quantity: float = 0.0
    avg_price: float = 0.0

    def market_value(self, price: float) -> float:
        return self.quantity * price

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.avg_price) * self.quantity


@dataclass
class AccountState:
    cash: float
    equity: float
    positions: dict[str, Position] = field(default_factory=dict)
    timestamp: datetime | None = None
