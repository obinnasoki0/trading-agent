"""Config loading. Reads a YAML file if present, else sensible defaults.

Kept intentionally small -- the point is that every risk knob lives in one
place and is visible at a glance.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field

from .core.risk import RiskLimits, RiskTier


@dataclass
class NewsConfig:
    enabled: bool = False
    provider: str = "stub"     # stub | rss | live (polled RSS) | alpaca (push stream)
    weight: float = 0.3        # blend weight vs. technical signal
    limit: int = 20            # headlines per symbol
    poll_seconds: int = 60     # live feed poll cadence
    max_age_seconds: int = 3600  # how long a headline stays "fresh"


@dataclass
class FundamentalsConfig:
    enabled: bool = False
    provider: str = "stub"     # stub (offline) | yfinance (free, flaky)
    weight: float = 0.2        # blend weight vs. technical signal (keep modest)


@dataclass
class AgentConfig:
    symbols: list[str] = field(default_factory=lambda: ["AAPL", "MSFT", "SPY"])
    strategy: str = "sma_crossover"
    strategy_params: dict = field(default_factory=dict)
    starting_cash: float = 10_000.0
    commission: float = 0.0
    slippage_bps: float = 1.0
    broker: str = "paper"          # paper | alpaca | robinhood_mcp | robinhood
    asset_class: str = "equity"    # equity | crypto (crypto => 24/7 on Alpaca)
    allow_live: bool = False       # must be True to place real orders
    data_source: str = "synthetic"  # synthetic | yfinance | csv
    lookback_days: int = 400
    # Autonomy: how the unattended loop behaves.
    session: str = "equity"        # equity | extended | always (crypto/24-7)
    interval_seconds: int = 900    # seconds between autonomous decision cycles
    risk_profile: str = "medium"   # low | medium | aggressive (if `risk` not explicit)
    risk: RiskLimits = field(default_factory=lambda: RiskLimits.medium())
    # Equity-based tiers: automatically switch limits as the balance grows.
    risk_tiers: list = field(default_factory=list)
    news: NewsConfig = field(default_factory=NewsConfig)
    fundamentals: FundamentalsConfig = field(default_factory=FundamentalsConfig)

    def to_dict(self) -> dict:
        return asdict(self)


def _limits_from_spec(spec: dict) -> RiskLimits:
    """Build a tier's RiskLimits from a profile name plus any field overrides."""
    limits = RiskLimits.from_profile(spec["profile"]) if "profile" in spec else RiskLimits.medium()
    fields = set(RiskLimits.__dataclass_fields__)
    for key, val in spec.items():
        if key in fields:
            setattr(limits, key, val)
    return limits


def load(path: str | None = None) -> AgentConfig:
    if path and os.path.exists(path):
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pip install pyyaml to load YAML config") from exc
        with open(path) as fh:
            raw = yaml.safe_load(fh) or {}
        news = NewsConfig(**raw.pop("news", {})) if "news" in raw else NewsConfig()
        fundamentals = (FundamentalsConfig(**raw.pop("fundamentals", {}))
                        if "fundamentals" in raw else FundamentalsConfig())
        if "risk" in raw:
            risk = RiskLimits(**raw.pop("risk"))
        else:
            risk = RiskLimits.from_profile(raw.get("risk_profile", "medium"))
        tiers = [RiskTier(min_equity=float(spec.get("min_equity", 0)),
                          limits=_limits_from_spec(spec))
                 for spec in raw.pop("risk_tiers", [])]
        return AgentConfig(risk=risk, news=news, fundamentals=fundamentals,
                           risk_tiers=tiers, **raw)
    return AgentConfig()
