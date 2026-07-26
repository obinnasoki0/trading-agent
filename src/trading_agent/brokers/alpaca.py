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

_CRYPTO_QUOTES = ("USDT", "USDC", "USD", "BTC")


def _normalize_symbol(symbol: str, asset_class: str = "") -> str:
    """Alpaca reports crypto positions as 'LTCUSD' but orders/config use
    'LTC/USD'. Insert the slash so position lookups match the traded symbol --
    without this the engine never sees the holding, re-buys every cycle, and
    stops/take-profits never fire."""
    s = str(symbol)
    if "/" in s or "crypto" not in str(asset_class).lower():
        return s
    for quote in _CRYPTO_QUOTES:
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[:-len(quote)]}/{quote}"
    return s


class AlpacaBroker(Broker):
    name = "alpaca"

    def __init__(self, paper: bool = True, asset_class: str = "equity"):
        self.paper = paper
        self.is_live = not paper
        self.asset_class = asset_class  # "equity" | "crypto"
        # Alpaca supports shorting equities on margin (paper included), but crypto
        # is spot-only -- no shorts. Gate the engine accordingly.
        self.supports_short = asset_class != "crypto"
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
            sym = _normalize_symbol(p.symbol, getattr(p, "asset_class", ""))
            out[sym] = Position(sym, float(p.qty), float(p.avg_entry_price))
        return out

    def account(self) -> AccountState:
        acct = self._client().get_account()
        cash = float(acct.cash)
        equity = float(getattr(acct, "equity", cash) or cash)
        # Gross = |market value| of longs + shorts, so the risk manager's gross
        # cap counts short exposure too. Alpaca reports these directly.
        longs = float(getattr(acct, "long_market_value", 0) or 0)
        shorts = float(getattr(acct, "short_market_value", 0) or 0)
        gross = abs(longs) + abs(shorts)
        return AccountState(cash=cash, equity=equity, gross_value=gross,
                            positions=self.positions(), timestamp=datetime.now())

    def submit(self, order: Order) -> Order:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        # Crypto is spot-only on Alpaca: a SELL that would open/extend a short is
        # invalid. The engine shouldn't route one here, but guard anyway.
        if self.asset_class == "crypto" and order.side is Side.SELL:
            held = self.positions().get(_normalize_symbol(order.symbol, self.asset_class))
            held_qty = held.quantity if held else 0.0
            if order.quantity - held_qty > 1e-9:
                order.status = OrderStatus.REJECTED
                order.broker_id = "error: crypto is spot-only (cannot short)"
                return order

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
        # Market orders fill asynchronously, so filled_qty is usually 0 at submit
        # time. Report the submitted quantity so logs reflect what was ordered.
        raw_qty = getattr(resp, "filled_qty", None)
        try:
            filled_qty = float(raw_qty) if raw_qty is not None else 0.0
        except (TypeError, ValueError):
            filled_qty = 0.0
        order.filled_quantity = filled_qty or round(order.quantity, 6)
        order.status = OrderStatus.FILLED if order.broker_id else OrderStatus.REJECTED
        return order

    def cancel(self, broker_id: str) -> None:
        self._client().cancel_order_by_id(broker_id)
