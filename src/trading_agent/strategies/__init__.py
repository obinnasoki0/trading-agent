"""Strategy registry. Add new strategies here to expose them to the CLI."""

from __future__ import annotations

from .base import Strategy
from .momentum import Momentum
from .rsi_reversion import RsiReversion
from .sma_crossover import SmaCrossover

REGISTRY: dict[str, type[Strategy]] = {
    SmaCrossover.name: SmaCrossover,
    RsiReversion.name: RsiReversion,
    Momentum.name: Momentum,
}


def build(name: str, **params) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"unknown strategy {name!r}; available: {sorted(REGISTRY)}")
    return REGISTRY[name](**params)
