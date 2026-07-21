from datetime import datetime

from trading_agent.core.data import SyntheticData
from trading_agent.signals.news import (
    Headline,
    KeywordSentiment,
    NewsSignalSource,
    StubNewsProvider,
)
from trading_agent.strategies.blended import BlendedStrategy
from trading_agent.strategies.sma_crossover import SmaCrossover


def test_keyword_sentiment_direction():
    scorer = KeywordSentiment()
    pos = [Headline("X", "Company beats earnings, shares surge to record")]
    neg = [Headline("X", "Company misses, faces lawsuit and layoffs amid recession")]
    assert scorer.score(pos) > 0
    assert scorer.score(neg) < 0
    assert scorer.score([]) == 0.0


def test_sentiment_is_bounded():
    scorer = KeywordSentiment()
    many = [Headline("X", "surge rally record beats upgrade wins strong boost")] * 5
    assert -1.0 <= scorer.score(many) <= 1.0


def test_news_signal_source_with_stub():
    provider = StubNewsProvider({"AAPL": ["Apple beats and surges to record high"]})
    src = NewsSignalSource(provider=provider)
    assert src.sentiment("AAPL") > 0
    assert src.sentiment("UNKNOWN") == 0.0


def test_blended_strategy_shifts_with_news():
    data = SyntheticData().history("AAPL", datetime(2022, 1, 1), datetime(2024, 1, 1))
    base = SmaCrossover(fast=10, slow=30)
    bullish = StubNewsProvider({"AAPL": ["Apple beats, surges, record, upgrade, strong"]})
    blended = BlendedStrategy(base, NewsSignalSource(provider=bullish), w_tech=0.5, w_news=0.5)

    base_sig = base.generate("AAPL", data)
    blended_sig = blended.generate("AAPL", data)
    # Bullish news should push the blended signal at or above the raw technical one.
    assert blended_sig.strength >= base_sig.strength - 1e-9
