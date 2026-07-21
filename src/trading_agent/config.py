"""Config loading. Reads a YAML file if present, else sensible defaults.

Kept intentionally small -- the point is that every risk knob lives in one
place and is visible at a glance.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field

from .core.risk import RiskLimits


@dataclass
class NewsConfig:
    enabled: bool = False
    provider: str = "stub"     # stub | rss | live (polled RSS) | alpaca (push stream)
    weight: float = 0.3        # blend weight vs. technical signal
    limit: int = 20            # headlines per symbol
    poll_seconds: int = 60     # live feed poll cadence
    max_age_seconds: int = 3600  # how long a headline stays "fresh"


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
    risk_profile: str = "medium"   # low | medium (used if `risk` not given explicitly)
    risk: RiskLimits = field(default_factory=lambda: RiskLimits.medium())
    news: NewsConfig = field(default_factory=NewsConfig)

    def to_dict(self) -> dict:
        return asdict(self)


def load(path: str | None = None) -> AgentConfig:
    if path and os.path.exists(path):
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pip install pyyaml to load YAML config") from exc
        with open(path) as fh:
            raw = yaml.safe_load(fh) or {}
        news = NewsConfig(**raw.pop("news", {})) if "news" in raw else NewsConfig()
        if "risk" in raw:
            risk = RiskLimits(**raw.pop("risk"))
        else:
            risk = RiskLimits.from_profile(raw.get("risk_profile", "medium"))
        return AgentConfig(risk=risk, news=news, **raw)
    return AgentConfig()
