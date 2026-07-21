from datetime import datetime, timedelta, timezone

from trading_agent.signals.live import LiveNewsFeed
from trading_agent.signals.news import Headline, NewsProvider


class FakeProvider(NewsProvider):
    """Serves scripted headlines; each fetch can return different news."""

    def __init__(self):
        self.batches: dict[str, list[list[str]]] = {}
        self._idx: dict[str, int] = {}

    def script(self, symbol, batches):
        self.batches[symbol] = batches
        self._idx[symbol] = 0

    def fetch(self, symbol, limit=20):
        batches = self.batches.get(symbol, [])
        i = self._idx.get(symbol, 0)
        titles = batches[i] if i < len(batches) else (batches[-1] if batches else [])
        self._idx[symbol] = i + 1
        now = datetime.now(timezone.utc)
        return [Headline(symbol, t, now, "fake") for t in titles]


def test_poll_reports_only_new_headlines():
    prov = FakeProvider()
    prov.script("AAPL", [["Apple surges on record earnings"],
                         ["Apple surges on record earnings", "Apple wins upgrade"]])
    feed = LiveNewsFeed(provider=prov, symbols=["AAPL"])

    first = feed.poll_once()
    assert len(first["AAPL"]) == 1
    second = feed.poll_once()               # one already-seen, one new
    assert len(second["AAPL"]) == 1
    assert second["AAPL"][0].title == "Apple wins upgrade"


def test_sentiment_reflects_cached_news():
    prov = FakeProvider()
    prov.script("AAPL", [["Apple surges to record, strong upgrade, beats"]])
    feed = LiveNewsFeed(provider=prov, symbols=["AAPL"])
    feed.poll_once()
    assert feed.sentiment("AAPL") > 0
    assert feed.sentiment("MSFT") == 0.0    # never polled


def test_on_news_callback_fires():
    prov = FakeProvider()
    prov.script("AAPL", [["Apple plunges on lawsuit and probe"]])
    seen = []
    feed = LiveNewsFeed(provider=prov, symbols=["AAPL"],
                        on_news=lambda sym, hs: seen.append((sym, len(hs))))
    feed.poll_once()
    assert seen == [("AAPL", 1)]


def test_stale_headlines_are_pruned():
    prov = FakeProvider()
    feed = LiveNewsFeed(provider=prov, symbols=["AAPL"], max_age_seconds=1)
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    feed._cache["AAPL"] = [Headline("AAPL", "old news beats", old, "fake")]
    assert feed.sentiment("AAPL") == 0.0    # pruned as stale
