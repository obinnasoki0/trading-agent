from datetime import datetime

from trading_agent.core.data import SyntheticData
from trading_agent.signals.fundamentals import (
    FundamentalsScorer,
    FundamentalsSignalSource,
    StubFundamentals,
)
from trading_agent.signals.news import NewsSignalSource, StubNewsProvider
from trading_agent.strategies.blended import BlendedStrategy
from trading_agent.strategies.sma_crossover import SmaCrossover


def test_healthy_company_scores_positive():
    m = {"earnings_growth": 0.2, "revenue_growth": 0.15, "profit_margin": 0.25,
         "trailing_pe": 18, "peg_ratio": 1.0}
    assert FundamentalsScorer().score(m) > 0


def test_weak_company_scores_negative():
    m = {"earnings_growth": -0.2, "revenue_growth": -0.1, "profit_margin": -0.05,
         "trailing_pe": 80, "peg_ratio": 3.0}
    assert FundamentalsScorer().score(m) < 0


def test_unknown_fundamentals_are_neutral_and_bounded():
    assert FundamentalsScorer().score({}) == 0.0
    assert -1.0 <= FundamentalsScorer().score(
        {"earnings_growth": 9, "revenue_growth": 9, "profit_margin": 9}) <= 1.0
    src = FundamentalsSignalSource(provider=StubFundamentals())
    assert src.score("XYZ") == 0.0


def test_three_way_blend_includes_all_factors():
    data = SyntheticData().history("AAPL", datetime(2022, 1, 1), datetime(2024, 1, 1))
    base = SmaCrossover(fast=10, slow=30)
    news = NewsSignalSource(provider=StubNewsProvider({"AAPL": ["a calm day"]}))
    fund = FundamentalsSignalSource(
        provider=StubFundamentals({"AAPL": {"earnings_growth": 0.3, "profit_margin": 0.2}}))
    blended = BlendedStrategy(base, news=news, fundamentals=fund,
                              w_tech=0.5, w_news=0.25, w_fund=0.25)
    sig = blended.generate("AAPL", data)
    assert "fund=" in sig.reason and "news=" in sig.reason
    assert abs(blended.w_tech + blended.w_news + blended.w_fund - 1.0) < 1e-9


def test_disabled_factors_reweight_to_technical():
    base = SmaCrossover(fast=10, slow=30)
    b = BlendedStrategy(base, news=None, fundamentals=None, w_tech=0.7, w_news=0.3, w_fund=0.2)
    assert b.w_news == 0.0 and b.w_fund == 0.0
    assert abs(b.w_tech - 1.0) < 1e-9
