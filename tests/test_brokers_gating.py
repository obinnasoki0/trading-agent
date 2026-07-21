"""The live brokers must never place a real order unless explicitly unlocked."""

from trading_agent.brokers.robinhood_mcp import RobinhoodMCPBroker
from trading_agent.core.models import Order, Side


def test_robinhood_mcp_dry_run_by_default():
    b = RobinhoodMCPBroker()  # defaults: dry_run=True, allow_live=False
    order = b.submit(Order("AAPL", Side.BUY, 1))
    assert order.status.value == "rejected"
    assert order.broker_id == "dry-run"


def test_robinhood_mcp_still_dry_run_without_allow_live():
    b = RobinhoodMCPBroker(allow_live=False, dry_run=False)
    order = b.submit(Order("AAPL", Side.BUY, 1))
    assert order.status.value == "rejected"
    assert order.broker_id == "dry-run"


def test_registry_builds_paper_without_sdk():
    from trading_agent import brokers
    from trading_agent.config import AgentConfig

    broker = brokers.build("paper", AgentConfig(), understood=False)
    assert broker.name == "paper"
    assert broker.is_live is False
