"""Fundamental-analysis signal -- company quality, blended with technical + news.

This is the "fundamentals in sync with technicals" piece. It scores each symbol
on a few plain financial-health metrics (earnings/revenue growth, profit margin,
valuation) into a single number in [-1, 1], which the blended strategy folds in
alongside the technical signal and news sentiment.

Design intent (honest): fundamentals move **quarterly**, so they don't drive
short-term trades -- they act as a *quality tilt/filter*. A strong technical +
news setup on a fundamentally healthy name gets a boost; the same setup on a
deteriorating company gets damped. There is no "perfect sync" that guarantees
profit -- this is a transparent heuristic you can tune, not magic.

Providers:
* ``StubFundamentals``    -- offline/deterministic; default so tests + offline
                             runs never hit the network.
* ``YFinanceFundamentals``-- free fundamentals via yfinance. Limited and
                             occasionally flaky; fails soft to neutral (0.0).

Fundamentals barely change intraday, so results are cached per process.
"""

from __future__ import annotations


class FundamentalsProvider:
    def metrics(self, symbol: str) -> dict:
        """Return a dict with any of: earnings_growth, revenue_growth,
        profit_margin, trailing_pe, peg_ratio. Missing keys are treated neutral."""
        raise NotImplementedError


class StubFundamentals(FundamentalsProvider):
    def __init__(self, canned: dict[str, dict] | None = None):
        self.canned = canned or {}

    def metrics(self, symbol: str) -> dict:
        return self.canned.get(symbol, {})


class YFinanceFundamentals(FundamentalsProvider):
    def metrics(self, symbol: str) -> dict:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError("pip install yfinance to use YFinanceFundamentals") from exc
        try:
            info = yf.Ticker(symbol).info  # network; can be slow/flaky
        except Exception:
            return {}
        return {
            "earnings_growth": info.get("earningsGrowth"),
            "revenue_growth": info.get("revenueGrowth"),
            "profit_margin": info.get("profitMargins"),
            "trailing_pe": info.get("trailingPE"),
            "peg_ratio": info.get("pegRatio"),
        }


def _clamp(x: float) -> float:
    return max(-1.0, min(1.0, x))


class FundamentalsScorer:
    """Turn raw metrics into a quality score in [-1, 1]. Transparent and tunable."""

    def score(self, m: dict) -> float:
        parts: list[float] = []

        for key in ("earnings_growth", "revenue_growth", "profit_margin"):
            v = m.get(key)
            if v is not None:
                # +10% growth/margin -> +0.5; saturates. Negative -> negative.
                parts.append(_clamp(float(v) * 5))

        pe = m.get("trailing_pe")
        if pe is not None and pe > 0:
            # Cheap-ish (<=15) favorable; rich (>=50) penalized; linear between.
            parts.append(_clamp((30 - float(pe)) / 30))

        peg = m.get("peg_ratio")
        if peg is not None and peg > 0:
            # PEG < 1 great, ~1.5 neutral, > 2 poor.
            parts.append(_clamp((1.5 - float(peg))))

        if not parts:
            return 0.0
        return _clamp(sum(parts) / len(parts))


class FundamentalsSignalSource:
    """Per-symbol fundamental quality in [-1, 1], cached per process."""

    def __init__(self, provider: FundamentalsProvider | None = None,
                 scorer: FundamentalsScorer | None = None):
        self.provider = provider or StubFundamentals()
        self.scorer = scorer or FundamentalsScorer()
        self._cache: dict[str, float] = {}

    def score(self, symbol: str) -> float:
        if symbol not in self._cache:
            try:
                self._cache[symbol] = self.scorer.score(self.provider.metrics(symbol))
            except Exception:
                self._cache[symbol] = 0.0  # fail soft: unknown fundamentals => neutral
        return self._cache[symbol]
