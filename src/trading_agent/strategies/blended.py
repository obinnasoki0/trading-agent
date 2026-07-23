"""Blend a technical strategy with news sentiment and fundamentals.

    final = w_tech * technical + w_news * news_sentiment + w_fund * fundamentals

News and fundamentals are *tilts*, not drivers: keep their weights modest so the
technical read stays in charge, while a healthy/deteriorating company and the
day's headlines nudge conviction up or down. The risk manager still caps
everything downstream. Weights are normalized over whichever factors are active,
so turning news or fundamentals off just reweights the rest.
"""

from __future__ import annotations

import pandas as pd

from ..core.models import Signal
from ..signals.fundamentals import FundamentalsSignalSource
from ..signals.news import NewsSignalSource
from .base import Strategy


class BlendedStrategy(Strategy):
    """Weighted blend of a base technical Strategy, news, and fundamentals."""

    name = "blended"

    def __init__(self, base: Strategy, news: NewsSignalSource | None = None,
                 fundamentals: FundamentalsSignalSource | None = None,
                 w_tech: float = 0.7, w_news: float = 0.3, w_fund: float = 0.0):
        self.base = base
        self.news = news              # None => news factor off
        self.fundamentals = fundamentals  # None => fundamentals factor off
        # Only weight the factors that are actually active, then normalize.
        w_news = w_news if news is not None else 0.0
        w_fund = w_fund if fundamentals is not None else 0.0
        total = w_tech + w_news + w_fund or 1.0
        self.w_tech = w_tech / total
        self.w_news = w_news / total
        self.w_fund = w_fund / total
        self.warmup = base.warmup

    def generate(self, symbol: str, history: pd.DataFrame) -> Signal:
        tech = self.base.generate(symbol, history)
        if len(history) < self.warmup:
            return tech
        news = self.news.sentiment(symbol) if self.news is not None else 0.0
        fund = self.fundamentals.score(symbol) if self.fundamentals is not None else 0.0
        strength = self.w_tech * tech.strength + self.w_news * news + self.w_fund * fund
        reason = (f"tech={tech.strength:+.2f} news={news:+.2f} "
                  f"fund={fund:+.2f} -> {strength:+.2f}")
        return Signal(symbol=symbol, strength=strength, timestamp=history.index[-1], reason=reason)
