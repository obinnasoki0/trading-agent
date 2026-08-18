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


def _fmt_price(p: float | None) -> str:
    """Sub-cent assets (e.g. SHIB) round to 0.00 at 2 dp; show more precision."""
    p = p or 0.0
    if 0 < abs(p) < 0.01:
        return f"{p:.8f}"
    return f"{p:,.2f}"


class TradingEngine:
    def __init__(self, broker: Broker, strategy: Strategy, risk: RiskManager,
                 data: DataProvider, symbols: list[str], lookback_days: int = 400,
                 max_positions: int = 0, allow_short: bool = False,
                 short_size_mult: float = 0.5, profit_bank_cooldown_cycles: int = 3,
                 let_winners_run: bool = False):
        self.broker = broker
        self.strategy = strategy
        self.risk = risk
        self.data = data
        self.symbols = symbols
        self.lookback_days = lookback_days
        # 0 = evaluate/buy every symbol independently; >0 = rank the universe and
        # hold at most this many names (cross-sectional selection).
        self.max_positions = max_positions
        # Open shorts on bearish setups (only if the broker supports it).
        self.allow_short = allow_short
        self.short_size_mult = short_size_mult
        # After the daily profit ratchet banks a name, don't reopen it for this
        # many cycles -- forces rotation into fresh signals instead of rebuying.
        self.profit_bank_cooldown_cycles = profit_bank_cooldown_cycles
        # Let winners run: replace the fixed take-profit with a TRAILING stop, so a
        # position rides as long as the trend holds and only exits on a pullback
        # from its peak or an analysis reversal (handled in _act_on_signal).
        self.let_winners_run = let_winners_run
        self._entry_price: dict[str, float] = {}
        self._peak_price: dict[str, float] = {}  # best price since entry (trailing)
        self._cooldown: dict[str, int] = {}  # symbol -> cycle it's tradable again
        self._cycle = 0
        self._day: str | None = None

    def _on_cooldown(self, symbol: str) -> bool:
        return self._cooldown.get(symbol, 0) > self._cycle

    def _can_short(self) -> bool:
        return self.allow_short and getattr(self.broker, "supports_short", False)

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
        self._cycle += 1

        kill = self.risk.kill_switch_triggered(account.equity)
        if kill:
            for symbol, pos in list(self.broker.positions().items()):
                if abs(pos.quantity) < 1e-9:
                    continue
                # Longs are sold, shorts are bought back (covered).
                side = Side.SELL if pos.quantity > 0 else Side.BUY
                self._submit(symbol, side, abs(pos.quantity), actions,
                             reason=f"KILL SWITCH: {kill}")
            return actions

        universe = symbols if symbols is not None else self.symbols
        if self.max_positions and symbols is None:
            self._ranked_step(universe, actions)
        else:
            for symbol in universe:
                self._evaluate(symbol, actions)

        # Daily profit ratchet runs AFTER the scan, so held positions are priced
        # at this cycle's fresh marks. On a trigger, bank the winners; the freed
        # cash and slots redeploy into fresh signals next cycle (banked names sit
        # out a short cooldown so it rotates instead of rebuying them).
        if self.risk.harvest_due(self.broker.account().equity):
            self._bank_profits(actions)
        return actions

    def _bank_profits(self, actions: list[str]) -> None:
        """Close every position currently in profit to realize the day's gains."""
        banked = 0
        target = self.risk.limits.daily_profit_target_pct
        for symbol, pos in list(self.broker.positions().items()):
            if abs(pos.quantity) < 1e-9:
                continue
            price = self.broker.last_price(symbol)
            if not price or price <= 0:
                continue
            entry = self._entry_price.get(symbol) or getattr(pos, "avg_price", 0.0) or 0.0
            if not entry:
                continue
            in_profit = price > entry if pos.quantity > 0 else price < entry
            if not in_profit:
                continue
            side = Side.SELL if pos.quantity > 0 else Side.BUY
            self._submit(symbol, side, abs(pos.quantity), actions,
                         reason=f"bank profit (daily +{target:.1%} ratchet)")
            self._cooldown[symbol] = self._cycle + self.profit_bank_cooldown_cycles
            banked += 1
        if banked == 0:
            actions.append(f"daily +{target:.1%} target hit: no positions in profit to bank")

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
        """Stop-loss / take-profit for a held position (long OR short). Returns
        True if it closed the position."""
        pos = self.broker.positions().get(symbol)
        if not pos or abs(pos.quantity) < 1e-9:
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

        stop = self.risk.limits.stop_loss_pct
        tp = self.risk.limits.take_profit_pct

        if self.let_winners_run:
            # Trailing stop off the best price seen -> ride the trend, lock gains,
            # exit only on a pullback. No fixed cap; a reversal exit still fires in
            # _act_on_signal when the blended (tech+news+fund) signal turns.
            if pos.quantity > 0:  # LONG
                peak = max(self._peak_price.get(symbol, entry), price)
                self._peak_price[symbol] = peak
                if price <= peak * (1 - stop):
                    self._submit(symbol, Side.SELL, pos.quantity, actions,
                                 reason=f"trailing stop ({stop:.0%} off peak {_fmt_price(peak)})")
                    return True
            else:  # SHORT
                trough = min(self._peak_price.get(symbol, entry), price)
                self._peak_price[symbol] = trough
                if price >= trough * (1 + stop):
                    self._submit(symbol, Side.BUY, abs(pos.quantity), actions,
                                 reason=f"trailing stop ({stop:.0%} off low {_fmt_price(trough)})")
                    return True
            return False

        # Fixed mode: hard stop from entry + fixed take-profit.
        if pos.quantity < 0:  # SHORT: profits when price falls, loses when it rises
            if price >= entry * (1 + stop):
                self._submit(symbol, Side.BUY, abs(pos.quantity), actions, reason="stop-loss (short)")
                return True
            if tp > 0 and price <= entry * (1 - tp):
                self._submit(symbol, Side.BUY, abs(pos.quantity), actions, reason="take-profit (short)")
                return True
            return False
        # LONG
        if price <= entry * (1 - stop):
            self._submit(symbol, Side.SELL, pos.quantity, actions, reason="stop-loss")
            return True
        if tp > 0 and price >= entry * (1 + tp):
            self._submit(symbol, Side.SELL, pos.quantity, actions, reason="take-profit")
            return True
        return False

    def _try_open(self, symbol, price, signal, side, actions) -> tuple[bool, str]:
        """Size and open a new position -- a long (BUY) or a short (SELL). Shorts
        are sized smaller via short_size_mult. Returns (opened?, reason)."""
        account = self.broker.account()
        mult = getattr(signal, "size_mult", 1.0)
        if side is Side.SELL:
            mult *= self.short_size_mult
        qty = self.risk.size_for(symbol, price, account.equity) * mult
        order = Order(symbol, side, qty, OrderType.MARKET, created_at=datetime.now())
        decision = self.risk.review(order, price, account)
        if decision.approved and decision.order:
            # On a notional-capable broker, send the BUY as a dollar amount ("$X
            # worth") -- cleaner than a tiny fractional share count on a small
            # account. Derived from the risk-approved quantity, so all caps hold.
            if side is Side.BUY and getattr(self.broker, "supports_notional", False):
                decision.order.dollar_amount = round(decision.order.quantity * price, 2)
            self._submit_order(decision.order, price, actions, signal.reason, opening=True)
            return True, "ok"
        return False, f"{symbol}: {decision.reason}"

    def _act_on_signal(self, symbol, price, pos, signal, actions,
                       collect: list | None = None) -> None:
        """Turn a signal into an open/close given the current position. When
        ``collect`` is provided (ranked mode), new opens are appended there as
        (score, symbol, price, signal, side) instead of executed immediately."""
        is_long = bool(pos and pos.quantity > 0)
        is_short = bool(pos and pos.quantity < 0)
        if signal.strength > 0.05:
            if is_short:  # signal flipped bullish -> cover the short
                self._submit(symbol, Side.BUY, abs(pos.quantity), actions,
                             reason=f"cover: {signal.reason}")
            elif not pos and not self._on_cooldown(symbol):
                if collect is not None:
                    collect.append((signal.strength, symbol, price, signal, Side.BUY))
                else:
                    ok, reason = self._try_open(symbol, price, signal, Side.BUY, actions)
                    if not ok:
                        actions.append(f"buy vetoed: {reason}")
        elif signal.strength < -0.05:
            if is_long:  # signal flipped bearish -> sell the long
                self._submit(symbol, Side.SELL, pos.quantity, actions, reason=signal.reason)
            elif not pos and self._can_short() and not self._on_cooldown(symbol):
                if collect is not None:
                    collect.append((-signal.strength, symbol, price, signal, Side.SELL))
                else:
                    ok, reason = self._try_open(symbol, price, signal, Side.SELL, actions)
                    if not ok:
                        actions.append(f"short vetoed: {reason}")

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
        self._act_on_signal(symbol, price, pos, signal, actions)

    def _ranked_step(self, universe: list[str], actions: list[str]) -> None:
        """Scan the universe, handle exits, then open the top-ranked setups (longs
        and, if enabled, shorts) up to the open-slot budget."""
        candidates: list = []  # (score, symbol, price, signal, side)
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
            self._act_on_signal(symbol, price, pos, signal, actions, collect=candidates)

        slots = max(0, self.max_positions - len(self.broker.positions()))
        candidates.sort(key=lambda c: c[0], reverse=True)  # strongest conviction first
        opened = 0
        last_veto = ""
        for _score, symbol, price, signal, side in candidates[:slots]:
            ok, reason = self._try_open(symbol, price, signal, side, actions)
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

    def _submit_order(self, order: Order, price: float, actions: list[str], reason: str,
                      opening: bool = False):
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
            # Record entry on an opening order (long BUY or short SELL); clear it
            # on any close/cover. Keying on `opening` -- not side -- is what makes
            # short entries (a SELL) track their entry price correctly.
            if opening:
                self._entry_price[order.symbol] = filled.filled_price or price
                self._peak_price[order.symbol] = filled.filled_price or price
            else:
                self._entry_price.pop(order.symbol, None)
                self._peak_price.pop(order.symbol, None)
            amt = f" (~${order.dollar_amount:.2f})" if order.dollar_amount else ""
            actions.append(f"[{tag}] {order.side.value} {filled.filled_quantity:.4f} "
                           f"{order.symbol}{amt} @ {_fmt_price(filled.filled_price or price)} ({reason})")
        elif is_dry:
            amt = f" (~${order.dollar_amount:.2f})" if order.dollar_amount else ""
            actions.append(f"[{tag}] would {order.side.value} {order.quantity:.4f} "
                           f"{order.symbol}{amt} @ {_fmt_price(price)} ({reason})")
        else:
            actions.append(f"[{tag}] {order.side.value} {order.symbol} not filled: "
                           f"{filled.status.value} {filled.broker_id or ''}")
