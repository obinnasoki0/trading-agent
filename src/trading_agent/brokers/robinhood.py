"""Robinhood adapter via the unofficial ``robin_stocks`` library.

⚠️  READ THIS FIRST
-------------------
Robinhood has **no official trading API**. This adapter talks to reverse-
engineered private endpoints. That means:

* It **violates Robinhood's Terms of Service**, and automated access can get
  your account restricted or permanently locked.
* Robinhood actively deploys bot detection (device checks, challenges). This
  code may break without warning when they change endpoints.
* You are responsible for any consequences of using it.

Because of all that, this adapter is **live and gated**: it refuses to place
real orders unless you both provide credentials *and* pass ``allow_live=True``.
For anything other than "I accept the risks and want real execution", use
``PaperBroker`` (the default) or an API-sanctioned broker such as Alpaca.

Auth: set ROBINHOOD_USERNAME / ROBINHOOD_PASSWORD (and handle MFA when prompted).
Never commit these -- use a .env file that is gitignored.
"""

from __future__ import annotations

import os
from datetime import datetime

from ..core.models import (
    AccountState,
    Order,
    OrderStatus,
    Position,
    Side,
)
from .base import Broker


class RobinhoodBroker(Broker):
    name = "robinhood"
    is_live = True

    def __init__(self, allow_live: bool = False, dry_run: bool = True):
        # dry_run: read account/prices from Robinhood, but never actually place orders.
        self.dry_run = dry_run
        self.allow_live = allow_live
        self._rh = None

    def _client(self):
        if self._rh is not None:
            return self._rh
        try:
            import robin_stocks.robinhood as rh
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                "robin_stocks is not installed. `pip install robin_stocks`. "
                "Note: automating Robinhood violates its ToS -- see this module's docstring."
            ) from exc

        user = os.getenv("ROBINHOOD_USERNAME")
        pw = os.getenv("ROBINHOOD_PASSWORD")
        if not user or not pw:
            raise RuntimeError("Set ROBINHOOD_USERNAME and ROBINHOOD_PASSWORD in the environment.")
        mfa = os.getenv("ROBINHOOD_MFA_CODE")
        rh.login(username=user, password=pw, mfa_code=mfa, store_session=True)
        self._rh = rh
        return rh

    def last_price(self, symbol: str) -> float:
        rh = self._client()
        quote = rh.stocks.get_latest_price(symbol, includeExtendedHours=False)
        return float(quote[0]) if quote and quote[0] else 0.0

    def positions(self) -> dict[str, Position]:
        rh = self._client()
        out: dict[str, Position] = {}
        for holding in rh.account.build_holdings().items():
            symbol, data = holding
            qty = float(data.get("quantity", 0) or 0)
            if qty:
                out[symbol] = Position(symbol=symbol, quantity=qty,
                                       avg_price=float(data.get("average_buy_price", 0) or 0))
        return out

    def account(self) -> AccountState:
        rh = self._client()
        profile = rh.profiles.load_account_profile()
        cash = float(profile.get("cash", 0) or 0)
        positions = self.positions()
        equity = cash + sum(p.quantity * self.last_price(s) for s, p in positions.items())
        return AccountState(cash=cash, equity=equity, positions=positions,
                            timestamp=datetime.now())

    def submit(self, order: Order) -> Order:
        # Hard safety gate: never touch a real market unless explicitly unlocked.
        if self.dry_run or not self.allow_live:
            order.status = OrderStatus.REJECTED
            order.broker_id = "dry-run"
            print(f"[DRY-RUN] would {order.side.value} {order.quantity:.4f} {order.symbol}")
            return order

        rh = self._client()
        try:
            if order.side is Side.BUY:
                resp = rh.orders.order_buy_market(order.symbol, quantity=round(order.quantity, 4))
            else:
                resp = rh.orders.order_sell_market(order.symbol, quantity=round(order.quantity, 4))
        except Exception as exc:  # pragma: no cover - network path
            order.status = OrderStatus.REJECTED
            order.broker_id = f"error: {exc}"
            return order

        order.broker_id = resp.get("id") if isinstance(resp, dict) else None
        order.status = OrderStatus.FILLED if order.broker_id else OrderStatus.REJECTED
        return order

    def cancel(self, broker_id: str) -> None:
        rh = self._client()
        rh.orders.cancel_stock_order(broker_id)
