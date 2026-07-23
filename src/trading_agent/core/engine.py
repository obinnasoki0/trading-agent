"""Live/paper trading loop.

One ``step()`` pulls fresh history, generates signals, and routes orders through
the risk manager to whatever broker is attached. Run it on a schedule (cron,
your own loop) once you've validated a strategy in backtests.

Safety posture:
* If the broker is live but not explicitly unlocked, orders are dry-run only.
* The drawdown kill switch and stop-loss run every step, same as the backtester.
"""

from __future__ import annotations

from datetime import datetime

from ..brokers.base import Broker
from ..strategies.base import Strategy
from .data import DataProvider, make_window
from .models import Order, OrderType, Side
from .risk import RiskManager


class TradingEngine:
    def __init__(self, broker: Broker, strategy: Strategy, risk: RiskManager,
                 data: DataProvider, symbols: list[str], lookback_days: int = 400):
        self.broker = broker
        self.strategy = strategy
        self.risk = risk
        self.data = data
        self.symbols = symbols
        self.lookback_days = lookback_days
        self._entry_price: dict[str, float] = {}
        self._day: str | None = None

    def _roll_day(self, equity: float) -> None:
        today = datetime.now().date().isoformat()
        if today != self._day:
            self._day = today
            self.risk.start_day(equity)

    def step(self, symbols: list[str] | None = None) -> list[str]:
        """Run one decision cycle. ``symbols=None`` evaluates all configured
        symbols; passing a subset (e.g. from a news event) evaluates just those.
        The account-level kill switch always runs first regardless."""
        actions: list[str] = []
        account = self.broker.account()
        self._roll_day(account.equity)

        kill = self.risk.kill_switch_triggered(account.equity)
        if kill:
            for symbol in list(self.broker.positions()):
                self._submit(symbol, Side.SELL, self.broker.positions()[symbol].quantity, actions,
                             reason=f"KILL SWITCH: {kill}")
            return actions

        for symbol in (symbols if symbols is not None else self.symbols):
            self._evaluate(symbol, actions)
        return actions

    def _evaluate(self, symbol: str, actions: list[str]) -> None:
        start, end = make_window(self.lookback_days)
        try:
            history = self.data.history(symbol, start, end)
        except Exception as exc:
            actions.append(f"{symbol}: data error: {exc}")
            return
        if history.empty:
            return

        price = float(history["close"].iloc[-1])
        # Paper broker prices itself from the data feed; live brokers ignore this.
        if hasattr(self.broker, "set_price"):
            self.broker.set_price(symbol, price)
        account = self.broker.account()
        pos = self.broker.positions().get(symbol)

        # Exits first: stop-loss caps the downside, take-profit locks in gains.
        entry = self._entry_price.get(symbol)
        if pos and entry:
            if price <= entry * (1 - self.risk.limits.stop_loss_pct):
                self._submit(symbol, Side.SELL, pos.quantity, actions, reason="stop-loss")
                return
            tp = self.risk.limits.take_profit_pct
            if tp > 0 and price >= entry * (1 + tp):
                self._submit(symbol, Side.SELL, pos.quantity, actions, reason="take-profit")
                return

        if len(history) < self.strategy.warmup:
            return

        signal = self.strategy.generate(symbol, history)
        if signal.strength > 0.05 and not pos:
            qty = self.risk.size_for(symbol, price, account.equity) * getattr(signal, "size_mult", 1.0)
            order = Order(symbol, Side.BUY, qty, OrderType.MARKET, created_at=datetime.now())
            decision = self.risk.review(order, price, account)
            if decision.approved and decision.order:
                self._submit_order(decision.order, price, actions, signal.reason)
            else:
                actions.append(f"{symbol}: buy vetoed ({decision.reason})")
        elif signal.strength < -0.05 and pos:
            self._submit(symbol, Side.SELL, pos.quantity, actions, reason=signal.reason)

    def _submit(self, symbol, side, qty, actions, reason):
        order = Order(symbol, side, qty, OrderType.MARKET, created_at=datetime.now())
        self._submit_order(order, self.broker.last_price(symbol), actions, reason)

    def _submit_order(self, order: Order, price: float, actions: list[str], reason: str):
        filled = self.broker.submit(order)
        # A live broker held in dry-run marks orders with broker_id "dry-run".
        # Tag honestly so a dry-run never prints "[LIVE]".
        is_dry = filled.broker_id == "dry-run"
        if not self.broker.is_live:
            tag = "PAPER"
        elif is_dry:
            tag = "DRY-RUN"
        else:
            tag = "LIVE"

        if filled.status.value == "filled":
            if order.side is Side.BUY and filled.filled_price:
                self._entry_price[order.symbol] = filled.filled_price
            else:
                self._entry_price.pop(order.symbol, None)
            actions.append(f"[{tag}] {order.side.value} {filled.filled_quantity:.4f} "
                           f"{order.symbol} @ {filled.filled_price or price:.2f} ({reason})")
        elif is_dry:
            actions.append(f"[{tag}] would {order.side.value} {order.quantity:.4f} "
                           f"{order.symbol} @ {price:.2f} ({reason})")
        else:
            actions.append(f"[{tag}] {order.side.value} {order.symbol} not filled: "
                           f"{filled.status.value} {filled.broker_id or ''}")
