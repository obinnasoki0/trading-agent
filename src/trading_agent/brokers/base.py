"""Broker interface. The engine only ever talks to this, never to a vendor SDK.

Swapping PaperBroker for RobinhoodBroker (or an Alpaca one later) changes
nothing about the strategy, risk, or engine code.
"""

from __future__ import annotations

from ..core.models import AccountState, Order, Position


class Broker:
    #: Human label used in logs/reports.
    name: str = "base"
    #: True if orders hit a real market with real money.
    is_live: bool = False

    def account(self) -> AccountState:
        raise NotImplementedError

    def positions(self) -> dict[str, Position]:
        raise NotImplementedError

    def last_price(self, symbol: str) -> float:
        raise NotImplementedError

    def submit(self, order: Order) -> Order:
        raise NotImplementedError

    def cancel(self, broker_id: str) -> None:
        raise NotImplementedError
