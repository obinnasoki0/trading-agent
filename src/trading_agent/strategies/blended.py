"""Blend a technical strategy with a news-sentiment tilt.

    final_strength = w_tech * technical_signal + w_news * news_sentiment

Sentiment is a *tilt*, not a driver: keep ``w_news`` modest (default 0.3) so a
noisy lexicon score can nudge sizing and confirm/deny the technical read, but
can't single-handedly pile into a position. The risk manager still caps
everything downstream.
"""

from __future__ import annotations

import pandas as pd

from ..core.models import Signal
from ..signals.news import NewsSignalSource
from .base import Strategy


class BlendedStrategy(Strategy):
    """Weighted blend of a base technical Strategy and news sentiment."""

    name = "blended"

    def __init__(self, base: Strategy, news: NewsSignalSource | None = None,
                 w_tech: float = 0.7, w_news: float = 0.3):
        self.base = base
        self.news = news or NewsSignalSource()
        total = w_tech + w_news
        self.w_tech = w_tech / total
        self.w_news = w_news / total
        self.warmup = base.warmup

    def generate(self, symbol: str, history: pd.DataFrame) -> Signal:
        tech = self.base.generate(symbol, history)
        if len(history) < self.warmup:
            return tech
        sentiment = self.news.sentiment(symbol)
        strength = self.w_tech * tech.strength + self.w_news * sentiment
        reason = f"tech={tech.strength:+.2f} news={sentiment:+.2f} -> {strength:+.2f}"
        return Signal(symbol=symbol, strength=strength, timestamp=history.index[-1], reason=reason)
