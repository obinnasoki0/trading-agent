"""Trend-following: fast SMA over slow SMA => long; below => flat."""

from __future__ import annotations

import pandas as pd

from ..core.models import Signal
from .base import Strategy
from .indicators import sma


class SmaCrossover(Strategy):
    """Trend-following via fast/slow SMA crossover."""

    name = "sma_crossover"

    def __init__(self, fast: int = 20, slow: int = 50):
        if fast >= slow:
            raise ValueError("fast window must be shorter than slow window")
        self.fast, self.slow = fast, slow
        self.warmup = slow + 1

    def generate(self, symbol: str, history: pd.DataFrame) -> Signal:
        if len(history) < self.warmup:
            return self._flat(symbol, history)
        close = history["close"]
        fast_now, slow_now = sma(close, self.fast).iloc[-1], sma(close, self.slow).iloc[-1]
        gap = (fast_now - slow_now) / slow_now if slow_now else 0.0
        strength = max(-1.0, min(1.0, gap * 20))  # scale the % gap into conviction
        reason = f"fast({self.fast})={fast_now:.2f} slow({self.slow})={slow_now:.2f}"
        return Signal(symbol=symbol, strength=strength, timestamp=history.index[-1], reason=reason)
