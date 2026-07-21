"""Live news feed -- react to headlines as they're published.

``LiveNewsFeed`` runs a background poller that keeps a rolling, de-duplicated
cache of recent headlines per symbol and exposes a fresh ``sentiment(symbol)``
that ``BlendedStrategy`` reads on every decision cycle. New headlines can also
fire an ``on_news`` callback so the engine can act event-driven, not just on its
timer.

Two feed backends:
* ``LiveNewsFeed`` + ``RSSNewsProvider`` -- free, no key, but *polled* (latency =
  your poll interval, and RSS itself lags publication by minutes). Fine to start.
* ``AlpacaNewsStream`` -- true **push** via Alpaca's news websocket (Benzinga),
  near-real-time. Requires the Alpaca SDK + keys.

⚠️  Honest latency note: "news as it's published" is a spectrum. Retail-grade
feeds (RSS, even Alpaca's) are seconds-to-minutes behind, and by the time a
headline is public it is often already priced in. Treat this as a fast *tilt*,
size it small, and never remove the risk caps.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from .news import Headline, KeywordSentiment, NewsProvider, RSSNewsProvider


class LiveNewsFeed:
    """Polls a NewsProvider on a background thread; serves fresh sentiment."""

    def __init__(self, provider: NewsProvider | None = None, symbols: list[str] | None = None,
                 scorer: KeywordSentiment | None = None, poll_seconds: int = 60,
                 max_age_seconds: int = 3600, limit: int = 20, on_news=None):
        self.provider = provider or RSSNewsProvider()
        self.symbols = symbols or []
        self.scorer = scorer or KeywordSentiment()
        self.poll_seconds = poll_seconds
        self.max_age_seconds = max_age_seconds
        self.limit = limit
        self.on_news = on_news
        self._cache: dict[str, list[Headline]] = {}
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def _prune(self, headlines: list[Headline]) -> list[Headline]:
        now = datetime.now(timezone.utc)
        return [h for h in headlines
                if h.published is None or (now - h.published).total_seconds() <= self.max_age_seconds]

    def poll_once(self) -> dict[str, list[Headline]]:
        """Fetch each symbol once; return only the *newly seen* headlines."""
        fresh: dict[str, list[Headline]] = {}
        for symbol in self.symbols:
            try:
                headlines = self.provider.fetch(symbol, self.limit)
            except Exception:
                continue
            new = [h for h in headlines if h.title not in self._seen]
            if not new and symbol in self._cache:
                continue
            with self._lock:
                for h in new:
                    self._seen.add(h.title)
                combined = self._prune(self._cache.get(symbol, []) + new)
                self._cache[symbol] = combined
            if new:
                fresh[symbol] = new
                if self.on_news:
                    try:
                        self.on_news(symbol, new)
                    except Exception:
                        pass
        return fresh

    def sentiment(self, symbol: str) -> float:
        """Fresh sentiment in [-1, 1] from recent (non-stale) headlines."""
        with self._lock:
            recent = self._prune(self._cache.get(symbol, []))
            self._cache[symbol] = recent
        return self.scorer.score(recent)

    def latest(self, symbol: str, n: int = 5) -> list[Headline]:
        with self._lock:
            return list(self._cache.get(symbol, []))[-n:]

    # -- background lifecycle --------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def _loop():
            while not self._stop.is_set():
                self.poll_once()
                self._stop.wait(self.poll_seconds)

        self._thread = threading.Thread(target=_loop, name="live-news-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


class AlpacaNewsStream:
    """Near-real-time push news via Alpaca's websocket. Optional; needs SDK+keys.

    Feeds headlines into a shared ``LiveNewsFeed`` cache so the rest of the
    system is backend-agnostic.
    """

    def __init__(self, feed: LiveNewsFeed, symbols: list[str]):
        self.feed = feed
        self.symbols = symbols
        self._stream = None

    def start(self) -> None:  # pragma: no cover - network/websocket path
        import os

        from alpaca.data.live.news import NewsDataStream

        key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise RuntimeError("Set ALPACA_API_KEY and ALPACA_SECRET_KEY for the news stream.")
        self._stream = NewsDataStream(key, secret)

        async def _handler(news):
            for sym in (getattr(news, "symbols", None) or self.symbols):
                h = Headline(sym, getattr(news, "headline", ""),
                             getattr(news, "created_at", datetime.now(timezone.utc)), "alpaca")
                with self.feed._lock:
                    if h.title in self.feed._seen:
                        continue
                    self.feed._seen.add(h.title)
                    self.feed._cache.setdefault(sym, []).append(h)
                if self.feed.on_news:
                    self.feed.on_news(sym, [h])

        self._stream.subscribe_news(_handler, *self.symbols)
        self._stream.run()
