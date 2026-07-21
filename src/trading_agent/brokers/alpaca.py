"""Alpaca adapter -- the legal, API-sanctioned path to autonomous trading.

Why Alpaca:
* Official REST API, explicitly built for automation (no ToS gymnastics).
* Free, unlimited **paper trading** on a real endpoint.
* **Crypto trades 24/7** -- this is how you get genuine round-the-clock trading;
  set ``asset_class: crypto`` and use pairs like ``BTC/USD``.

Defaults to the **paper** endpoint. Live trading requires ``paper=False`` (wired
to ``allow_live`` + --i-understand-the-risks in the CLI).

Auth (never commit these -- use .env):
    ALPACA_API_KEY, ALPACA_SECRET_KEY

Install: pip install "trading-agent[alpaca]"
"""

from __future__ import annotations

import os
from datetime import datetime

from ..core.models import AccountState, Order, OrderStatus, Position, Side
from .base import Broker


class AlpacaBroker(Broker):
    name = "alpaca"

    def __init__(self, paper: bool = True, asset_class: str = "equity"):
        self.paper = paper
        self.is_live = not paper
        self.asset_class = asset_class  # "equity" | "crypto"
        self._trading = None
        self._stock_data = None
        self._crypto_data = None

    # -- lazy SDK clients -------------------------------------------------
    def _keys(self) -> tuple[str, str]:
        key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise RuntimeError("Set ALPACA_API_KEY and ALPACA_SECRET_KEY in the environment.")
        return key, secret

    def _client(self):
        if self._trading is None:
            try:
                from alpaca.trading.client import TradingClient
            except ImportError as exc:  # pragma: no cover - optional dep
                raise RuntimeError('Install Alpaca SDK: pip install "trading-agent[alpaca]"') from exc
            key, secret = self._keys()
            self._trading = TradingClient(key, secret, paper=self.paper)
        return self._trading

    def _data(self):
        try:
            from alpaca.data.historical import (
                CryptoHistoricalDataClient,
                StockHistoricalDataClient,
            )
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError('Install Alpaca SDK: pip install "trading-agent[alpaca]"') from exc
        key, secret = self._keys()
        if self.asset_class == "crypto":
            if self._crypto_data is None:
                self._crypto_data = CryptoHistoricalDataClient(key, secret)
            return self._crypto_data
        if self._stock_data is None:
            self._stock_data = StockHistoricalDataClient(key, secret)
        return self._stock_data

    # -- Broker interface -------------------------------------------------
    def last_price(self, symbol: str) -> float:
        data = self._data()
        if self.asset_class == "crypto":
            from alpaca.data.requests import CryptoLatestTradeRequest
            resp = data.get_crypto_latest_trade(CryptoLatestTradeRequest(symbol_or_symbols=symbol))
        else:
            from alpaca.data.requests import StockLatestTradeRequest
            resp = data.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol))
        trade = resp.get(symbol) if isinstance(resp, dict) else resp
        return float(getattr(trade, "price", 0) or 0)

    def positions(self) -> dict[str, Position]:
        out: dict[str, Position] = {}
        for p in self._client().get_all_positions():
            out[p.symbol] = Position(p.symbol, float(p.qty), float(p.avg_entry_price))
        return out

    def account(self) -> AccountState:
        acct = self._client().get_account()
        cash = float(acct.cash)
        equity = float(getattr(acct, "equity", cash) or cash)
        return AccountState(cash=cash, equity=equity, positions=self.positions(),
                            timestamp=datetime.now())

    def submit(self, order: Order) -> Order:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        side = OrderSide.BUY if order.side is Side.BUY else OrderSide.SELL
        # Crypto supports GTC and trades 24/7; equities use DAY.
        tif = TimeInForce.GTC if self.asset_class == "crypto" else TimeInForce.DAY
        req = MarketOrderRequest(symbol=order.symbol, qty=round(order.quantity, 6),
                                 side=side, time_in_force=tif)
        try:
            resp = self._client().submit_order(req)
        except Exception as exc:  # pragma: no cover - network path
            order.status = OrderStatus.REJECTED
            order.broker_id = f"error: {exc}"
            return order
        order.broker_id = str(getattr(resp, "id", "")) or None
        filled = getattr(resp, "filled_avg_price", None)
        order.filled_price = float(filled) if filled else None
        order.filled_quantity = float(getattr(resp, "filled_qty", order.quantity) or order.quantity)
        order.status = OrderStatus.FILLED if order.broker_id else OrderStatus.REJECTED
        return order

    def cancel(self, broker_id: str) -> None:
        self._client().cancel_order_by_id(broker_id)
