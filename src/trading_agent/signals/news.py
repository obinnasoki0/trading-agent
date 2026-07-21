"""News + sentiment signal layer.

This is the hook for reacting to current events -- geopolitics, banking,
industry, and tech headlines -- alongside technical analysis. The design:

    NewsProvider (fetch headlines) -> KeywordSentiment (score) -> NewsSignalSource
    -> a per-symbol sentiment in [-1, 1] that a strategy can blend with technicals.

Providers
---------
* ``StubNewsProvider`` -- offline, deterministic. Default so everything runs and
  tests pass without network or API keys.
* ``RSSNewsProvider``  -- pulls a free Google-News-style RSS feed per symbol.
  Requires network; no API key. Good enough to prototype, not production-grade.

⚠️  Honest limitations
----------------------
Lexicon sentiment on headlines is a *weak* signal. It cannot understand nuance,
sarcasm, priced-in news, or second-order market reactions. Durable event-driven
alpha usually needs paid, low-latency feeds and far more sophisticated NLP.
Treat this as a tilt on top of a technical strategy, sized small -- not a
standalone money printer. Backtest any blend before trusting it with capital.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Headline:
    symbol: str
    title: str
    published: datetime | None = None
    source: str = ""


# A deliberately small, transparent finance/geopolitics lexicon. Extend freely.
_POSITIVE = {
    "beats", "beat", "surge", "surges", "rally", "record", "upgrade", "upgraded",
    "growth", "profit", "gains", "soar", "soars", "bullish", "outperform",
    "breakthrough", "approval", "wins", "strong", "boost", "expands", "deal",
    "partnership", "buyback", "raises", "tops", "rebound", "recovery",
}
_NEGATIVE = {
    "misses", "miss", "plunge", "plunges", "crash", "downgrade", "downgraded",
    "loss", "losses", "lawsuit", "probe", "recall", "bearish", "underperform",
    "warning", "warns", "cuts", "layoffs", "default", "sanctions", "war",
    "conflict", "inflation", "recession", "selloff", "fraud", "bankruptcy",
    "ban", "tariff", "tariffs", "slump", "weak", "fine", "hack", "breach",
}


class KeywordSentiment:
    """Lexicon scorer. Returns a score in [-1, 1] for a batch of headlines."""

    def __init__(self, positive=None, negative=None):
        self.positive = positive or _POSITIVE
        self.negative = negative or _NEGATIVE

    def score_text(self, text: str) -> int:
        tokens = {t.strip(".,!?:;\"'()").lower() for t in text.split()}
        return len(tokens & self.positive) - len(tokens & self.negative)

    def score(self, headlines: list[Headline]) -> float:
        if not headlines:
            return 0.0
        raw = sum(self.score_text(h.title) for h in headlines)
        # Normalize by count so a symbol with many headlines isn't over-weighted.
        norm = raw / max(1, len(headlines))
        return max(-1.0, min(1.0, norm))


class NewsProvider:
    def fetch(self, symbol: str, limit: int = 20) -> list[Headline]:
        raise NotImplementedError


class StubNewsProvider(NewsProvider):
    """Offline provider. Returns a fixed, neutral-ish set so runs are reproducible."""

    def __init__(self, canned: dict[str, list[str]] | None = None):
        self.canned = canned or {}

    def fetch(self, symbol: str, limit: int = 20) -> list[Headline]:
        titles = self.canned.get(symbol, [])
        now = datetime.now(timezone.utc)
        return [Headline(symbol, t, now, "stub") for t in titles[:limit]]


class RSSNewsProvider(NewsProvider):
    """Free RSS headlines (Google News query per symbol). Network required."""

    BASE = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

    def __init__(self, extra_terms: str = "stock", timeout: float = 8.0):
        self.extra_terms = extra_terms
        self.timeout = timeout

    def fetch(self, symbol: str, limit: int = 20) -> list[Headline]:
        query = urllib.parse.quote(f"{symbol} {self.extra_terms}")
        url = self.BASE.format(q=query)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "trading-agent/0.1"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                xml = resp.read().decode("utf-8", "ignore")
        except Exception:
            return []  # fail soft: no news => neutral, never crash the loop
        return self._parse(symbol, xml, limit)

    @staticmethod
    def _parse(symbol: str, xml: str, limit: int) -> list[Headline]:
        import re
        titles = re.findall(r"<title>(.*?)</title>", xml, flags=re.DOTALL)
        out = []
        for t in titles[1: limit + 1]:  # skip the feed's own <title>
            clean = re.sub(r"<!\[CDATA\[|\]\]>", "", t).strip()
            if clean:
                out.append(Headline(symbol, clean, datetime.now(timezone.utc), "google-rss"))
        return out


class NewsSignalSource:
    """Turns headlines into a per-symbol sentiment score in [-1, 1]."""

    def __init__(self, provider: NewsProvider | None = None,
                 scorer: KeywordSentiment | None = None, limit: int = 20):
        self.provider = provider or StubNewsProvider()
        self.scorer = scorer or KeywordSentiment()
        self.limit = limit

    def sentiment(self, symbol: str) -> float:
        headlines = self.provider.fetch(symbol, self.limit)
        return self.scorer.score(headlines)
