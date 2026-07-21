"""Broker registry.

* paper         -- simulated, default, safe.
* alpaca        -- official Alpaca API. Legal to automate; stocks + 24/7 crypto.
* robinhood_mcp -- official Robinhood Agentic Trading MCP (sanctioned).
* robinhood     -- legacy unofficial robin_stocks (ToS-violating; discouraged).
"""

from __future__ import annotations

from .base import Broker
from .paper import PaperBroker


def build(name: str, cfg, understood: bool) -> Broker:
    """Construct a broker from config. Imports vendor SDKs lazily."""
    if name == "paper":
        return PaperBroker(cfg.starting_cash, cfg.commission, cfg.slippage_bps)
    if name == "alpaca":
        from .alpaca import AlpacaBroker
        live = cfg.allow_live and understood
        if cfg.allow_live and not understood:
            print("Refusing Alpaca live trading without --i-understand-the-risks. Using paper.")
        return AlpacaBroker(paper=not live, asset_class=cfg.asset_class)
    if name == "robinhood_mcp":
        from .robinhood_mcp import RobinhoodMCPBroker
        live = cfg.allow_live and understood
        return RobinhoodMCPBroker(allow_live=live, dry_run=not live)
    if name == "robinhood":
        from .robinhood import RobinhoodBroker
        live = cfg.allow_live and understood
        return RobinhoodBroker(allow_live=live, dry_run=not live)
    raise KeyError(f"unknown broker {name!r}")


__all__ = ["Broker", "PaperBroker", "build"]
