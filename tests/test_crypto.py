"""Crypto wiring: config + provider selection. Live Alpaca calls need keys +
network, so those aren't exercised here -- we verify the plumbing lines up."""

import io

import pytest
import yaml

from trading_agent.config import AgentConfig, load
from trading_agent.core.data import AlpacaData


def _write(tmp_path, cfg: dict):
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return str(p)


def test_crypto_config_loads(tmp_path):
    path = _write(tmp_path, {
        "symbols": ["BTC/USD", "ETH/USD"],
        "broker": "alpaca", "asset_class": "crypto",
        "data_source": "alpaca", "session": "always",
        "risk_profile": "low",
    })
    cfg = load(path)
    assert cfg.asset_class == "crypto"
    assert cfg.broker == "alpaca"
    assert cfg.session == "always"
    assert cfg.symbols == ["BTC/USD", "ETH/USD"]


def test_data_provider_picks_alpaca_for_crypto():
    from trading_agent.cli import _data_provider
    cfg = AgentConfig(data_source="alpaca", asset_class="crypto")
    provider = _data_provider(cfg)
    assert isinstance(provider, AlpacaData)
    assert provider.asset_class == "crypto"


def test_alpaca_data_needs_sdk_message():
    # Without the alpaca SDK installed, history() raises a clear install hint
    # rather than a cryptic ImportError.
    provider = AlpacaData(asset_class="crypto")
    try:
        import alpaca  # noqa: F401
        pytest.skip("alpaca SDK is installed; skip the missing-dep path")
    except ImportError:
        pass
    from datetime import datetime
    with pytest.raises(RuntimeError, match="Alpaca SDK"):
        provider.history("BTC/USD", datetime(2024, 1, 1), datetime(2024, 2, 1))
