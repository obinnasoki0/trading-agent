"""External signal sources (news, sentiment, macro) that augment price data."""

from __future__ import annotations

from .news import (
    Headline,
    KeywordSentiment,
    NewsProvider,
    NewsSignalSource,
    RSSNewsProvider,
    StubNewsProvider,
)

__all__ = [
    "Headline",
    "KeywordSentiment",
    "NewsProvider",
    "NewsSignalSource",
    "RSSNewsProvider",
    "StubNewsProvider",
]
