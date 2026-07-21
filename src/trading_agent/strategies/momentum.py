"""Cross-sectional-style momentum: buy positive trailing return, exit on negative."""

from __future__ import annotations

import pandas as pd

from ..core.models import Signal
from .base import Strategy
from .indicators import rolling_return


class Momentum(Strategy):
    """Buy positive trailing-return momentum, exit on negative."""

    name = "momentum"

    def __init__(self, lookback: int = 90, threshold: float = 0.0):
        self.lookback, self.threshold = lookback, threshold
        self.warmup = lookback + 1

    def generate(self, symbol: str, history: pd.DataFrame) -> Signal:
        if len(history) < self.warmup:
            return self._flat(symbol, history)
        ret = rolling_return(history["close"], self.lookback).iloc[-1]
        if pd.isna(ret):
            return self._flat(symbol, history, reason="return undefined")
        strength = max(-1.0, min(1.0, (ret - self.threshold) * 3))
        return Signal(symbol=symbol, strength=strength, timestamp=history.index[-1],
                      reason=f"{self.lookback}d return={ret:.2%}")
