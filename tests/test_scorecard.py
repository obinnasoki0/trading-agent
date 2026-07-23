from datetime import datetime

import numpy as np
import pandas as pd

from trading_agent.signals.fundamentals import FundamentalsSignalSource, StubFundamentals
from trading_agent.signals.news import NewsSignalSource, StubNewsProvider
from trading_agent.strategies.scorecard import (
    ScorecardStrategy,
    _score_catalyst,
    _score_fundamentals,
    _score_trend,
    _score_valuation,
    score_trade,
)


def _series(prices):
    idx = pd.date_range("2021-01-01", periods=len(prices), freq="B")
    p = np.array(prices, dtype=float)
    return pd.DataFrame({"open": p, "high": p * 1.01, "low": p * 0.99,
                         "close": p, "volume": np.full(len(p), 1_000_000.0)}, index=idx)


def test_trend_scores_uptrend_high_and_downtrend_zero():
    up = _series(list(np.linspace(50, 150, 260)))       # steady climb
    down = _series(list(np.linspace(150, 50, 260)))     # steady fall
    assert _score_trend(up)[0] == 2
    assert _score_trend(down)[0] == 0


def test_catalyst_and_fundamentals_and_valuation_scores():
    assert _score_catalyst(0.4)[0] == 2
    assert _score_catalyst(-0.4)[0] == 0
    assert _score_catalyst(0.0)[0] == 1
    assert _score_fundamentals({"earnings_growth": 0.3, "profit_margin": 0.2})[0] == 2
    assert _score_fundamentals({"earnings_growth": -0.2})[0] == 0
    assert _score_valuation({"trailing_pe": 10, "peg_ratio": 1.0})[0] == 2
    assert _score_valuation({"trailing_pe": 80})[0] == 0
    assert _score_valuation({})[0] == 1                 # no data => neutral


def test_score_trade_totals_and_reward_risk():
    up = _series(list(np.linspace(50, 150, 260)))
    card = score_trade(up, news_score=0.3,
                       fund_metrics={"earnings_growth": 0.3, "trailing_pe": 12, "peg_ratio": 1.0},
                       stop_loss_pct=0.05)
    assert 0 <= card.total <= 12
    assert card.scores["trend"] == 2
    assert card.reward_risk >= 0


def test_scorecard_strategy_downtrend_signals_exit():
    down = _series(list(np.linspace(150, 50, 260)))
    strat = ScorecardStrategy(stop_loss_pct=0.05)
    sig = strat.generate("X", down)
    assert sig.strength == -1.0            # trend invalidation => exit/avoid
    assert "AVOID" in sig.reason


def test_scorecard_strategy_strong_setup_buys_full_and_sizes():
    up = _series(list(np.linspace(50, 150, 260)))
    news = NewsSignalSource(provider=StubNewsProvider({"X": ["surges beats record upgrade strong"]}))
    fund = FundamentalsSignalSource(
        provider=StubFundamentals({"X": {"earnings_growth": 0.3, "revenue_growth": 0.2,
                                         "profit_margin": 0.25, "trailing_pe": 12, "peg_ratio": 1.0}}))
    strat = ScorecardStrategy(news=news, fundamentals=fund, stop_loss_pct=0.05)
    sig = strat.generate("X", up)
    assert sig.strength > 0                # qualifies to buy
    assert sig.size_mult in (0.5, 1.0)
    assert "score=" in sig.reason


def test_scorecard_needs_warmup():
    short = _series(list(range(50)))       # < 200 bars
    sig = ScorecardStrategy().generate("X", short)
    assert sig.strength == 0.0
