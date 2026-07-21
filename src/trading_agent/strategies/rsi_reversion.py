"""Mean-reversion: buy oversold (RSI < lower), exit when overbought."""

from __future__ import annotations

import pandas as pd

from ..core.models import Signal
from .base import Strategy
from .indicators import rsi


class RsiReversion(Strategy):
    """Mean-reversion: buy oversold RSI, exit when overbought."""

    name = "rsi_reversion"

    def __init__(self, window: int = 14, lower: float = 30.0, upper: float = 70.0):
        self.window, self.lower, self.upper = window, lower, upper
        self.warmup = window + 1

    def generate(self, symbol: str, history: pd.DataFrame) -> Signal:
        if len(history) < self.warmup:
            return self._flat(symbol, history)
        value = rsi(history["close"], self.window).iloc[-1]
        if pd.isna(value):
            return self._flat(symbol, history, reason="rsi undefined")
        if value <= self.lower:
            strength = (self.lower - value) / self.lower  # deeper oversold => stronger
        elif value >= self.upper:
            strength = -1.0  # overbought => exit
        else:
            strength = 0.0
        return Signal(symbol=symbol, strength=strength, timestamp=history.index[-1],
                      reason=f"rsi({self.window})={value:.1f}")
