"""Config loading. Reads a YAML file if present, else sensible defaults.

Kept intentionally small -- the point is that every risk knob lives in one
place and is visible at a glance.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field

from .core.risk import RiskLimits


@dataclass
class AgentConfig:
    symbols: list[str] = field(default_factory=lambda: ["AAPL", "MSFT", "SPY"])
    strategy: str = "sma_crossover"
    strategy_params: dict = field(default_factory=dict)
    starting_cash: float = 10_000.0
    commission: float = 0.0
    slippage_bps: float = 1.0
    broker: str = "paper"          # paper | robinhood
    allow_live: bool = False       # must be True to place real orders
    data_source: str = "synthetic"  # synthetic | yfinance | csv
    lookback_days: int = 400
    risk: RiskLimits = field(default_factory=RiskLimits)

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
        risk = RiskLimits(**raw.pop("risk", {})) if "risk" in raw else RiskLimits()
        return AgentConfig(risk=risk, **raw)
    return AgentConfig()
