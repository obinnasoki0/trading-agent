"""Evidence-scorecard decision engine (from the trade-decision framework).

Instead of firing on one blended number, this scores each candidate across
**independent evidence categories** (0/1/2 each), gates entry on a minimum total
score AND a minimum reward/risk, and sizes by conviction (full vs half). It also
exits a held name when its technical trend breaks (technical invalidation).

Six categories (Section 13 of the framework):
  trend        -- price vs 50/200-day structure           (price)
  participation-- volume confirmation (accumulation/dist.) (volume)
  catalyst     -- news sentiment                           (headlines)
  fundamentals -- growth/margins/quality                   (yfinance, stocks)
  valuation    -- P/E, PEG vs sane ranges                  (yfinance, stocks)
  reward_risk  -- ATR-based stop vs structural target      (price)

Governance:
  total >= 10 AND rr >= 2.0  -> full-size long
  total >=  8 AND rr >= 1.5  -> half-size long
  trend == 0 (confirmed downtrend) -> exit / avoid
  otherwise                   -> no trade

⚠️  Honest limits (same data ceiling as the rest of the project):
* **Positioning** (options/futures/short-interest/funding) and **crypto
  on-chain** (MVRV/NVT/flows) categories are NOT scored -- we have no feed, so
  they'd be guesses. For crypto, fundamentals + valuation fall back to neutral.
* The scores are transparent heuristics, not institutional models. This enforces
  *discipline and confluence* (fewer, higher-conviction trades). It does not
  manufacture edge -- backtest before trusting it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..core.models import Signal
from .base import Strategy
from .indicators import atr, sma


@dataclass
class TradeScorecard:
    scores: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    reward_risk: float = 0.0

    @property
    def total(self) -> int:
        return sum(self.scores.values())

    def summary(self) -> str:
        return f"score={self.total}/12 rr={self.reward_risk:.1f} | " + " ".join(self.reasons)


def _score_trend(history: pd.DataFrame) -> tuple[int, str]:
    close = history["close"]
    if len(close) < 200:
        return 1, "trend:n/a"
    price = float(close.iloc[-1])
    s50 = float(sma(close, 50).iloc[-1])
    s200 = float(sma(close, 200).iloc[-1])
    if price < s200:
        return 0, "trend:down"          # technical invalidation
    if price > s200 and s50 > s200:
        return 2, "trend:up"
    return 1, "trend:mixed"


def _score_participation(history: pd.DataFrame) -> tuple[int, str]:
    vol, close = history["volume"], history["close"]
    if len(vol) < 20:
        return 1, "vol:n/a"
    avg20 = float(vol.iloc[-20:].mean())
    recent = float(vol.iloc[-5:].mean())
    ret5 = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) > 6 else 0.0
    rel = recent / avg20 if avg20 else 1.0
    if rel > 1.2 and ret5 > 0:
        return 2, f"vol:accum({rel:.1f}x)"
    if rel > 1.2 and ret5 < 0:
        return 0, f"vol:distrib({rel:.1f}x)"
    return 1, f"vol:flat({rel:.1f}x)"


def _score_catalyst(news_score: float) -> tuple[int, str]:
    if news_score > 0.15:
        return 2, f"news:+{news_score:.2f}"
    if news_score < -0.15:
        return 0, f"news:{news_score:.2f}"
    return 1, f"news:{news_score:.2f}"


def _score_fundamentals(metrics: dict) -> tuple[int, str]:
    if not metrics:
        return 1, "fund:n/a"
    vals = [metrics.get(k) for k in ("earnings_growth", "revenue_growth", "profit_margin")]
    vals = [float(v) for v in vals if v is not None]
    if not vals:
        return 1, "fund:n/a"
    avg = sum(vals) / len(vals)
    if avg > 0.10:
        return 2, f"fund:strong({avg:+.0%})"
    if avg < 0:
        return 0, f"fund:weak({avg:+.0%})"
    return 1, f"fund:ok({avg:+.0%})"


def _score_valuation(metrics: dict) -> tuple[int, str]:
    pe = metrics.get("trailing_pe") if metrics else None
    peg = metrics.get("peg_ratio") if metrics else None
    if not pe or pe <= 0:
        return 1, "val:n/a"
    if pe < 15 and (peg is None or peg < 1.5):
        return 2, f"val:cheap(pe{pe:.0f})"
    if pe > 40 or (peg and peg > 2.5):
        return 0, f"val:rich(pe{pe:.0f})"
    return 1, f"val:fair(pe{pe:.0f})"


def _reward_risk(history: pd.DataFrame, stop_loss_pct: float) -> tuple[float, int, str]:
    close = history["close"]
    price = float(close.iloc[-1])
    a = atr(history, 14).iloc[-1]
    atr_val = float(a) if a == a else price * stop_loss_pct  # NaN guard
    stop_dist = max(2 * atr_val, price * stop_loss_pct)
    recent_high = float(history["high"].iloc[-120:].max()) if len(history) >= 20 else price
    target = max(recent_high, price + 4 * atr_val)          # forward target
    rr = (target - price) / stop_dist if stop_dist > 0 else 0.0
    if rr >= 2.0:
        return rr, 2, f"rr:{rr:.1f}"
    if rr >= 1.5:
        return rr, 1, f"rr:{rr:.1f}"
    return rr, 0, f"rr:{rr:.1f}"


def score_trade(history: pd.DataFrame, news_score: float, fund_metrics: dict,
                stop_loss_pct: float) -> TradeScorecard:
    card = TradeScorecard()
    for key, (score, why) in {
        "trend": _score_trend(history),
        "participation": _score_participation(history),
        "catalyst": _score_catalyst(news_score),
        "fundamentals": _score_fundamentals(fund_metrics),
        "valuation": _score_valuation(fund_metrics),
    }.items():
        card.scores[key] = score
        card.reasons.append(why)
    rr, rr_score, rr_why = _reward_risk(history, stop_loss_pct)
    card.scores["reward_risk"] = rr_score
    card.reasons.append(rr_why)
    card.reward_risk = rr
    return card


class ScorecardStrategy(Strategy):
    """Evidence-scorecard entry gate with conviction sizing and trend-exit."""

    name = "scorecard"
    warmup = 200  # needs 200 bars for the 50/200 trend structure

    def __init__(self, news=None, fundamentals=None, stop_loss_pct: float = 0.05,
                 min_score: int = 8, full_score: int = 10, min_rr: float = 1.5):
        self.news = news
        self.fundamentals = fundamentals
        self.stop_loss_pct = stop_loss_pct
        self.min_score = min_score
        self.full_score = full_score
        self.min_rr = min_rr

    def generate(self, symbol: str, history: pd.DataFrame) -> Signal:
        if len(history) < self.warmup:
            return self._flat(symbol, history)
        ts = history.index[-1]
        news_score = self.news.sentiment(symbol) if self.news is not None else 0.0
        metrics = {}
        if self.fundamentals is not None:
            try:
                metrics = self.fundamentals.provider.metrics(symbol)
            except Exception:
                metrics = {}

        card = score_trade(history, news_score, metrics, self.stop_loss_pct)

        # Confirmed downtrend => technical invalidation: exit / stay out.
        if card.scores["trend"] == 0:
            return Signal(symbol, -1.0, ts, "AVOID trend-down | " + card.summary())
        if card.total >= self.full_score and card.reward_risk >= 2.0:
            return Signal(symbol, 1.0, ts, "BUY full | " + card.summary(), size_mult=1.0)
        if card.total >= self.min_score and card.reward_risk >= self.min_rr:
            return Signal(symbol, 0.5, ts, "BUY half | " + card.summary(), size_mult=0.5)
        return Signal(symbol, 0.0, ts, "NO-TRADE | " + card.summary())
