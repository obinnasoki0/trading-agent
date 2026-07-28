"""Event-driven backtester.

Walks bar-by-bar through history, asks the strategy for a signal, routes any
resulting order through the *same* RiskManager the live engine uses, and fills
against a PaperBroker. Because the risk/execution path is shared, a backtest is
a faithful preview of paper/live behavior -- not a separate, rosier simulation.

Includes a per-bar stop-loss check and the drawdown kill switch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..brokers.paper import PaperBroker
from ..strategies.base import Strategy
from .models import Order, OrderType, Side
from .risk import RiskManager


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: list[dict] = field(default_factory=list)

    @property
    def total_return(self) -> float:
        if len(self.equity_curve) < 2 or self.equity_curve.iloc[0] == 0:
            return 0.0
        return self.equity_curve.iloc[-1] / self.equity_curve.iloc[0] - 1

    @property
    def max_drawdown(self) -> float:
        curve = self.equity_curve
        if curve.empty:
            return 0.0
        peak = curve.cummax()
        return float((1 - curve / peak).max())

    @property
    def sharpe(self) -> float:
        rets = self.equity_curve.pct_change().dropna()
        if rets.empty or rets.std() == 0:
            return 0.0
        return float(np.sqrt(252) * rets.mean() / rets.std())

    def summary(self) -> dict:
        return {
            "total_return": round(self.total_return, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "sharpe": round(self.sharpe, 3),
            "trades": len(self.trades),
            "final_equity": round(float(self.equity_curve.iloc[-1]), 2) if len(self.equity_curve) else 0.0,
        }


class _ExecMixin:
    """Shared order logic for the single-symbol and portfolio backtesters.

    Expects ``self.broker`` (PaperBroker), ``self.risk`` (RiskManager),
    ``self._entry_price``, and ``self.allow_short`` / ``self.short_size_mult``
    on the instance.
    """

    def _act(self, symbol, strength, price, ts, account, trades, size_mult=1.0):
        pos = self.broker.positions().get(symbol)
        is_long = bool(pos and pos.quantity > 0)
        is_short = bool(pos and pos.quantity < 0)
        if strength > 0.05:
            if is_short:  # flipped bullish -> cover
                self._close(symbol, price, ts, trades, reason="cover")
            elif not pos:
                self._open(symbol, Side.BUY, price, ts, account, trades, size_mult)
        elif strength < -0.05:
            if is_long:  # flipped bearish -> sell
                self._close(symbol, price, ts, trades, reason="signal")
            elif not pos and getattr(self, "allow_short", False):
                self._open(symbol, Side.SELL, price, ts, account, trades,
                           size_mult * getattr(self, "short_size_mult", 0.5))

    def _open(self, symbol, side, price, ts, account, trades, size_mult):
        qty = self.risk.size_for(symbol, price, account.equity) * size_mult
        if qty <= 0:
            return
        order = Order(symbol, side, qty, OrderType.MARKET, created_at=ts)
        decision = self.risk.review(order, price, account)
        if decision.approved and decision.order:
            filled = self.broker.submit(decision.order)
            if filled.status.value == "filled":
                self._entry_price[symbol] = filled.filled_price
                self._peak_price[symbol] = filled.filled_price  # trailing-stop anchor
                trades.append({"ts": str(ts), "side": side.value, "qty": filled.filled_quantity,
                               "price": filled.filled_price, "reason": "signal"})

    def _close(self, symbol, price, ts, trades, reason):
        """Flatten a long (SELL) or a short (BUY-to-cover)."""
        pos = self.broker.positions().get(symbol)
        if not pos or abs(pos.quantity) < 1e-9:
            return
        side = Side.SELL if pos.quantity > 0 else Side.BUY
        order = Order(symbol, side, abs(pos.quantity), OrderType.MARKET, created_at=ts)
        filled = self.broker.submit(order)
        if filled.status.value == "filled":
            self._entry_price.pop(symbol, None)
            self._peak_price.pop(symbol, None)
            trades.append({"ts": str(ts), "side": side.value, "qty": filled.filled_quantity,
                           "price": filled.filled_price, "reason": reason})

    # Back-compat alias: existing call sites use _exit for closing a position.
    def _exit(self, symbol, price, ts, trades, reason):
        self._close(symbol, price, ts, trades, reason)

    def _check_stops(self, symbol, price, ts, trades) -> bool:
        """Direction-aware stop-loss / take-profit. Returns True if it closed.

        With ``let_winners_run`` the fixed take-profit is replaced by a trailing
        stop off the best price seen, so trends ride until they pull back."""
        pos = self.broker.positions().get(symbol)
        entry = self._entry_price.get(symbol)
        if not pos or not entry:
            return False
        stop = self.risk.limits.stop_loss_pct
        tp = self.risk.limits.take_profit_pct

        if getattr(self, "let_winners_run", False):
            peaks = self._peak_price
            if pos.quantity > 0:  # long
                peak = max(peaks.get(symbol, entry), price)
                peaks[symbol] = peak
                if price <= peak * (1 - stop):
                    self._close(symbol, price, ts, trades, reason="trailing stop")
                    peaks.pop(symbol, None)
                    return True
            else:  # short
                trough = min(peaks.get(symbol, entry), price)
                peaks[symbol] = trough
                if price >= trough * (1 + stop):
                    self._close(symbol, price, ts, trades, reason="trailing stop")
                    peaks.pop(symbol, None)
                    return True
            return False

        if pos.quantity < 0:  # short
            if price >= entry * (1 + stop):
                self._close(symbol, price, ts, trades, reason="stop-loss")
                return True
            if tp > 0 and price <= entry * (1 - tp):
                self._close(symbol, price, ts, trades, reason="take-profit")
                return True
            return False
        if price <= entry * (1 - stop):  # long
            self._close(symbol, price, ts, trades, reason="stop-loss")
            return True
        if tp > 0 and price >= entry * (1 + tp):
            self._close(symbol, price, ts, trades, reason="take-profit")
            return True
        return False


class Backtester(_ExecMixin):
    def __init__(self, strategy: Strategy, risk: RiskManager,
                 starting_cash: float = 10_000.0, commission: float = 0.0,
                 slippage_bps: float = 1.0, allow_short: bool = False,
                 short_size_mult: float = 0.5, let_winners_run: bool = False):
        self.strategy = strategy
        self.risk = risk
        self.broker = PaperBroker(starting_cash, commission, slippage_bps)
        self._entry_price: dict[str, float] = {}
        self._peak_price: dict[str, float] = {}
        self.allow_short = allow_short
        self.short_size_mult = short_size_mult
        self.let_winners_run = let_winners_run

    def run(self, symbol: str, data: pd.DataFrame) -> BacktestResult:
        equity_points: list[tuple] = []
        trades: list[dict] = []
        self.risk.start_day(self.broker.account().equity)

        for i in range(len(data)):
            window = data.iloc[: i + 1]
            bar = window.iloc[-1]
            ts = window.index[-1]
            price = float(bar["close"])
            self.broker.set_price(symbol, price)

            account = self.broker.account()
            self.risk.observe_equity(account.equity)

            # 1) Kill switch: liquidate everything on max-drawdown breach.
            kill = self.risk.kill_switch_triggered(account.equity)
            if kill and symbol in self.broker.positions():
                self._exit(symbol, price, ts, trades, reason=kill)
                equity_points.append((ts, self.broker.account().equity))
                continue

            # 2) Exits on the open position: stop-loss / take-profit (both sides).
            if self._check_stops(symbol, price, ts, trades):
                equity_points.append((ts, self.broker.account().equity))
                continue

            # 3) Strategy signal -> sized order -> risk gate -> fill.
            if len(window) >= self.strategy.warmup:
                signal = self.strategy.generate(symbol, window)
                self._act(symbol, signal.strength, price, ts, account, trades,
                          getattr(signal, "size_mult", 1.0))

            equity_points.append((ts, self.broker.account().equity))

        idx = [p[0] for p in equity_points]
        vals = [p[1] for p in equity_points]
        return BacktestResult(equity_curve=pd.Series(vals, index=idx), trades=trades)


class PortfolioBacktester(_ExecMixin):
    """Backtest a strategy across many symbols on ONE shared account.

    This validates what the live loop actually does: the same RiskManager gates
    every symbol, so cross-symbol caps (gross exposure, cash floor, daily-loss
    halt, drawdown kill switch) apply jointly -- unlike running N independent
    single-symbol backtests, which would each think they own the whole account.
    """

    def __init__(self, strategy: Strategy, risk: RiskManager,
                 starting_cash: float = 10_000.0, commission: float = 0.0,
                 slippage_bps: float = 1.0, allow_short: bool = False,
                 short_size_mult: float = 0.5, let_winners_run: bool = False):
        self.strategy = strategy
        self.risk = risk
        self.broker = PaperBroker(starting_cash, commission, slippage_bps)
        self._entry_price: dict[str, float] = {}
        self._peak_price: dict[str, float] = {}
        self.allow_short = allow_short
        self.short_size_mult = short_size_mult
        self.let_winners_run = let_winners_run

    def run(self, data: dict[str, pd.DataFrame]) -> BacktestResult:
        if not data:
            raise ValueError("no symbols to backtest")
        # Trade only on dates every symbol has, so prices are always defined.
        common = None
        for df in data.values():
            common = df.index if common is None else common.intersection(df.index)
        dates = list(common)
        if not dates:
            raise ValueError("symbols have no overlapping dates")

        equity_points: list[tuple] = []
        trades: list[dict] = []
        self.risk.start_day(self.broker.account().equity)

        for ts in dates:
            for sym, df in data.items():
                self.broker.set_price(sym, float(df.loc[ts, "close"]))

            account = self.broker.account()
            self.risk.observe_equity(account.equity)

            # Account-level kill switch: liquidate the whole book at once.
            kill = self.risk.kill_switch_triggered(account.equity)
            if kill:
                for sym in list(self.broker.positions()):
                    self._exit(sym, self.broker.last_price(sym), ts, trades, reason=kill)
                equity_points.append((ts, self.broker.account().equity))
                continue

            for sym, df in data.items():
                price = float(df.loc[ts, "close"])
                if self._check_stops(sym, price, ts, trades):
                    continue

                window = df.loc[:ts]
                if len(window) < self.strategy.warmup:
                    continue
                signal = self.strategy.generate(sym, window)
                self._act(sym, signal.strength, price, ts, self.broker.account(), trades,
                          getattr(signal, "size_mult", 1.0))

            equity_points.append((ts, self.broker.account().equity))

        idx = [p[0] for p in equity_points]
        vals = [p[1] for p in equity_points]
        return BacktestResult(equity_curve=pd.Series(vals, index=idx), trades=trades)
