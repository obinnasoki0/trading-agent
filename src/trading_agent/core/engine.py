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
                 data: DataProvider, symbols: list[str], lookback_days: int = 400,
                 max_positions: int = 0):
        self.broker = broker
        self.strategy = strategy
        self.risk = risk
        self.data = data
        self.symbols = symbols
        self.lookback_days = lookback_days
        # 0 = evaluate/buy every symbol independently; >0 = rank the universe and
        # hold at most this many names (cross-sectional selection).
        self.max_positions = max_positions
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
        The account-level kill switch always runs first regardless.

        With ``max_positions > 0`` and a full scan, candidates are ranked and
        only the strongest are opened (up to the cap) -- cross-sectional
        selection over a universe."""
        actions: list[str] = []
        account = self.broker.account()
        self._roll_day(account.equity)

        kill = self.risk.kill_switch_triggered(account.equity)
        if kill:
            for symbol in list(self.broker.positions()):
                self._submit(symbol, Side.SELL, self.broker.positions()[symbol].quantity, actions,
                             reason=f"KILL SWITCH: {kill}")
            return actions

        universe = symbols if symbols is not None else self.symbols
        if self.max_positions and symbols is None:
            self._ranked_step(universe, actions)
        else:
            for symbol in universe:
                self._evaluate(symbol, actions)
        return actions

    def _load(self, symbol: str, actions: list[str]):
        start, end = make_window(self.lookback_days)
        try:
            history = self.data.history(symbol, start, end)
        except Exception as exc:
            actions.append(f"{symbol}: data error: {exc}")
            return None
        if history.empty:
            return None
        price = float(history["close"].iloc[-1])
        if hasattr(self.broker, "set_price"):  # paper broker prices from the feed
            self.broker.set_price(symbol, price)
        return history, price

    def _handle_exit(self, symbol: str, price: float, actions: list[str]) -> bool:
        """Stop-loss / take-profit for a held position. Returns True if it sold."""
        pos = self.broker.positions().get(symbol)
        if not pos:
            return False
        entry = self._entry_price.get(symbol)
        if entry is None:
            # Recover entry price across restarts from the broker's reported
            # average, so stops/take-profits protect positions opened before this
            # process started. Without this, a restart orphans them.
            entry = getattr(pos, "avg_price", 0.0) or 0.0
            if entry:
                self._entry_price[symbol] = entry
        if not entry:
            return False
        if price <= entry * (1 - self.risk.limits.stop_loss_pct):
            self._submit(symbol, Side.SELL, pos.quantity, actions, reason="stop-loss")
            return True
        tp = self.risk.limits.take_profit_pct
        if tp > 0 and price >= entry * (1 + tp):
            self._submit(symbol, Side.SELL, pos.quantity, actions, reason="take-profit")
            return True
        return False

    def _try_buy(self, symbol, price, signal, actions) -> tuple[bool, str]:
        account = self.broker.account()
        qty = self.risk.size_for(symbol, price, account.equity) * getattr(signal, "size_mult", 1.0)
        order = Order(symbol, Side.BUY, qty, OrderType.MARKET, created_at=datetime.now())
        decision = self.risk.review(order, price, account)
        if decision.approved and decision.order:
            self._submit_order(decision.order, price, actions, signal.reason)
            return True, "ok"
        return False, f"{symbol}: {decision.reason}"

    def _evaluate(self, symbol: str, actions: list[str]) -> None:
        loaded = self._load(symbol, actions)
        if loaded is None:
            return
        history, price = loaded
        pos = self.broker.positions().get(symbol)
        if pos and self._handle_exit(symbol, price, actions):
            return
        if len(history) < self.strategy.warmup:
            return
        signal = self.strategy.generate(symbol, history)
        if signal.strength > 0.05 and not pos:
            ok, reason = self._try_buy(symbol, price, signal, actions)
            if not ok:
                actions.append(f"buy vetoed: {reason}")
        elif signal.strength < -0.05 and pos:
            self._submit(symbol, Side.SELL, pos.quantity, actions, reason=signal.reason)

    def _ranked_step(self, universe: list[str], actions: list[str]) -> None:
        """Scan the universe, handle exits, then open the top-ranked buys up to
        the open-slot budget (max_positions minus current holdings)."""
        candidates = []  # (strength, symbol, price, signal)
        for symbol in universe:
            loaded = self._load(symbol, actions)
            if loaded is None:
                continue
            history, price = loaded
            pos = self.broker.positions().get(symbol)
            if pos and self._handle_exit(symbol, price, actions):
                continue
            if len(history) < self.strategy.warmup:
                continue
            signal = self.strategy.generate(symbol, history)
            if signal.strength < -0.05 and pos:
                self._submit(symbol, Side.SELL, pos.quantity, actions, reason=signal.reason)
            elif signal.strength > 0.05 and not pos:
                candidates.append((signal.strength, symbol, price, signal))

        slots = max(0, self.max_positions - len(self.broker.positions()))
        candidates.sort(key=lambda c: c[0], reverse=True)  # strongest conviction first
        opened = 0
        last_veto = ""
        for _strength, symbol, price, signal in candidates[:slots]:
            ok, reason = self._try_buy(symbol, price, signal, actions)
            if ok:
                opened += 1
            else:
                last_veto = reason
        if opened == 0:
            why = f" — {last_veto}" if last_veto else ""
            actions.append(f"scanned {len(universe)}: {len(candidates)} qualified, "
                           f"{slots} slot(s) open, none filled{why}")

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
