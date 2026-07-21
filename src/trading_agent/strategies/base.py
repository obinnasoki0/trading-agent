"""Strategy interface.

A strategy is a pure function of price history -> Signal. It never touches the
broker, cash, or position sizing -- that separation is what keeps risk control
in one place and makes strategies trivial to unit test and backtest.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from ..core.models import Signal


class Strategy:
    #: Minimum number of bars required before the strategy emits real signals.
    warmup: int = 1
    name: str = "base"

    def generate(self, symbol: str, history: pd.DataFrame) -> Signal:
        """Return a Signal for the latest bar in ``history``.

        ``history`` is OHLCV indexed by timestamp, oldest first, with the most
        recent bar last.
        """
        raise NotImplementedError

    def _flat(self, symbol: str, history: pd.DataFrame, reason: str = "warmup") -> Signal:
        ts = history.index[-1] if len(history) else datetime.now()
        return Signal(symbol=symbol, strength=0.0, timestamp=ts, reason=reason)
