"""Robinhood scanner -> dynamic universe. The broker turns a scan's rows into a
clean symbol list, and the engine refreshes its candidate universe from that each
cycle (always unioning in held names so exits are never orphaned)."""

from trading_agent.brokers.robinhood_mcp import RobinhoodMCPBroker
from trading_agent.core.engine import TradingEngine
from trading_agent.core.models import Position
from trading_agent.core.risk import RiskLimits, RiskManager


class _Res:
    """Stand-in for an MCP CallToolResult carrying structured content."""
    def __init__(self, payload):
        self.structuredContent = payload


def test_run_scan_extracts_and_normalizes_symbols():
    b = RobinhoodMCPBroker()
    b._call = lambda op, payload: _Res({"results": [
        {"symbol": "AAPL"}, {"symbol": "nvda"}, {"ticker": "MSFT"}, {"symbol": "AAPL"},
    ]})
    # Uppercased, de-duplicated, order preserved.
    assert b.run_scan("sc1") == ["AAPL", "NVDA", "MSFT"]


def test_run_scan_empty_is_empty_list():
    b = RobinhoodMCPBroker()
    b._call = lambda op, payload: _Res({"results": []})
    assert b.run_scan("sc1") == []


class _FakeBroker:
    def positions(self):
        return {"HELD": Position("HELD", 1.0, 10.0)}


def _engine(provider):
    return TradingEngine(_FakeBroker(), strategy=None, risk=RiskManager(RiskLimits()),
                         data=None, symbols=["FALLBACK"], universe_provider=provider)


def test_resolve_universe_unions_held_names_first():
    eng = _engine(lambda: ["AAA", "BBB"])
    out = eng._resolve_universe([])
    assert out[0] == "HELD"                 # held names lead, so exits always run
    assert "AAA" in out and "BBB" in out
    assert "FALLBACK" not in out            # provider returned names -> no fallback


def test_resolve_universe_falls_back_on_error():
    def boom():
        raise RuntimeError("scan down")
    actions: list = []
    eng = _engine(boom)
    out = eng._resolve_universe(actions)
    assert "FALLBACK" in out and "HELD" in out
    assert any("scan failed" in a for a in actions)


def test_no_provider_uses_static_symbols():
    eng = _engine(None)
    assert eng._resolve_universe([]) == ["FALLBACK"]
