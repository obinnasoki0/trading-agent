"""Official Robinhood Agentic Trading adapter (via Robinhood's MCP server).

This is the **sanctioned** way to trade Robinhood programmatically, launched by
Robinhood in 2026. Unlike the legacy ``robinhood.py`` (unofficial ``robin_stocks``
endpoints, ToS-violating), this talks to Robinhood's official MCP server:

    https://agent.robinhood.com/mcp/trading   (OAuth; your password is never seen)

Safety design (enforced by Robinhood, not just this code):
* Orders can be placed **only** in a dedicated, separately-funded *Agentic*
  account. Every other Robinhood account is read-only. Blast radius is capped to
  whatever you choose to fund that account with.
* Equities only at launch (options/other asset classes come later).

Two ways to use Robinhood Agentic Trading with this project
-----------------------------------------------------------
1. **Agent-driven** (what Robinhood built for): connect the MCP to Claude Code /
   Claude Desktop and let the agent analyze + trade conversationally. Auth is
   handled for you by ``claude mcp add`` (OAuth in the browser). Best for
   adaptive, hands-on use. See README.
2. **Loop-driven** (this adapter): our deterministic, risk-managed engine calls
   the MCP as its broker, for unattended 24/7 operation with the tested risk
   caps and kill switch in front of every order.

⚠️  Verification note
--------------------
The exact MCP *tool names/parameters* must be confirmed against the live server
(they can change during beta). Call :meth:`list_tools` after authenticating and
map them in ``TOOL_MAP`` below. Until then this adapter defaults to **dry-run**
and will not place real orders. It also requires ``allow_live=True``.

Requires the official MCP SDK:  pip install "trading-agent[robinhood]"  (mcp>=1.0)
Auth token: set ROBINHOOD_MCP_TOKEN (an OAuth access token for the MCP), or use
the agent-driven model in (1) which manages OAuth for you.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime

from ..core.models import AccountState, Order, OrderStatus, Position, Side
from .base import Broker

MCP_URL = os.getenv("ROBINHOOD_MCP_URL", "https://agent.robinhood.com/mcp/trading")

# Map our operations -> Robinhood MCP tool names.
# place/cancel names confirmed against the live server (2026-07); the read-side
# names are best guesses that discover_and_map()/verify-robinhood will correct
# automatically at runtime.
TOOL_MAP = {
    "account": "get_account",
    "positions": "get_positions",
    "quote": "get_quote",
    "place_order": "place_equity_order",    # confirmed live tool name
    "cancel_order": "cancel_equity_order",  # confirmed live tool name
}


class RobinhoodMCPBroker(Broker):
    name = "robinhood_mcp"
    is_live = True

    def __init__(self, allow_live: bool = False, dry_run: bool = True,
                 url: str | None = None, tool_map: dict | None = None):
        self.allow_live = allow_live
        self.dry_run = dry_run
        self.url = url or MCP_URL
        self.tool_map = tool_map or dict(TOOL_MAP)
        self._token = os.getenv("ROBINHOOD_MCP_TOKEN")

    # -- MCP plumbing -----------------------------------------------------
    def _headers(self) -> dict:
        if not self._token:
            raise RuntimeError(
                "No ROBINHOOD_MCP_TOKEN set. Either export an OAuth access token, "
                "or use the agent-driven model (connect the MCP to Claude Code, "
                "which handles OAuth for you)."
            )
        return {"Authorization": f"Bearer {self._token}"}

    async def _call_async(self, tool: str, arguments: dict | None = None):
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError('Install the MCP SDK: pip install "trading-agent[robinhood]"') from exc

        async with streamablehttp_client(self.url, headers=self._headers()) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments or {})
                return result

    def _call(self, op: str, arguments: dict | None = None):
        tool = self.tool_map[op]
        return asyncio.run(self._call_async(tool, arguments))

    def list_tool_details(self) -> list[tuple[str, str]]:
        """Discover (name, description) for every tool the live MCP exposes."""
        async def _run():
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
            async with streamablehttp_client(self.url, headers=self._headers()) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    return [(t.name, getattr(t, "description", "") or "") for t in tools.tools]
        return asyncio.run(_run())

    def list_tools(self) -> list[str]:
        return [name for name, _ in self.list_tool_details()]

    def discover_and_map(self, verbose: bool = True) -> dict:
        """Connect, list the real tools, and auto-map them to our operations by
        keyword. Updates ``self.tool_map`` in place and returns it.

        This is what makes the adapter resilient to Robinhood's exact tool names
        (which can shift during beta) without hand-editing code."""
        details = self.list_tool_details()
        mapping = _auto_map(details)
        if verbose:
            print("Robinhood MCP tools discovered:")
            for name, desc in details:
                print(f"  - {name}: {desc[:70]}")
            print("Auto-mapped operations:")
            for op, tool in mapping.items():
                print(f"  {op:12s} -> {tool or '(unmapped!)'}")
        self.tool_map.update({k: v for k, v in mapping.items() if v})
        return self.tool_map

    # -- Broker interface -------------------------------------------------
    # NOTE: response parsing below is defensive and will need to match the live
    # tool output schema once verified via list_tools().
    def last_price(self, symbol: str) -> float:
        res = self._call("quote", {"symbol": symbol})
        return _extract_float(res, ("price", "last_price", "last_trade_price"))

    def positions(self) -> dict[str, Position]:
        res = self._call("positions")
        out: dict[str, Position] = {}
        for row in _extract_rows(res):
            sym = row.get("symbol")
            qty = float(row.get("quantity", 0) or 0)
            if sym and qty:
                out[sym] = Position(sym, qty, float(row.get("average_price", 0) or 0))
        return out

    def account(self) -> AccountState:
        res = self._call("account")
        data = _extract_obj(res)
        cash = float(data.get("buying_power", data.get("cash", 0)) or 0)
        positions = self.positions()
        equity = cash + sum(p.quantity * self.last_price(s) for s, p in positions.items())
        return AccountState(cash=cash, equity=equity, positions=positions, timestamp=datetime.now())

    def submit(self, order: Order) -> Order:
        if self.dry_run or not self.allow_live:
            order.status = OrderStatus.REJECTED
            order.broker_id = "dry-run"
            print(f"[DRY-RUN] would {order.side.value} {order.quantity:.4f} {order.symbol} "
                  f"via Robinhood MCP")
            return order
        try:
            res = self._call("place_order", {
                "symbol": order.symbol,
                "side": order.side.value,
                "quantity": round(order.quantity, 6),
                "type": order.order_type.value,
                **({"limit_price": order.limit_price} if order.limit_price else {}),
            })
        except Exception as exc:  # pragma: no cover - network path
            order.status = OrderStatus.REJECTED
            order.broker_id = f"error: {exc}"
            return order
        data = _extract_obj(res)
        order.broker_id = data.get("id") or data.get("order_id")
        order.status = OrderStatus.FILLED if order.broker_id else OrderStatus.REJECTED
        order.filled_quantity = order.quantity
        return order

    def cancel(self, broker_id: str) -> None:
        self._call("cancel_order", {"order_id": broker_id})


# -- auto-mapping: match discovered tool names to our operations -------------
# Each op lists (must-have-any, nice-to-have) keyword sets scored against a
# tool's name + description. Highest score wins; ties prefer the shorter name.
_OP_KEYWORDS = {
    "cancel_order": (("cancel",), ("order", "equity")),
    "place_order": (("place", "submit", "buy", "sell", "trade"), ("order", "equity")),
    "quote": (("quote", "price", "last_trade", "market_data"), ("get",)),
    "positions": (("position", "holding"), ("get", "list")),
    "account": (("account", "balance", "buying_power", "portfolio"), ("get",)),
}

# Order-side ops must never bind to options/crypto tools -- this engine trades
# equities only on Robinhood (e.g. pick place_equity_order over place_option_order).
_ORDER_OPS = {"place_order", "cancel_order"}
_EXCLUDED_ASSET_WORDS = ("option", "crypto")


def _auto_map(details: list[tuple[str, str]]) -> dict:
    mapping: dict[str, str] = {}
    used: set[str] = set()
    # Resolve cancel before place so "cancel_order" isn't grabbed by "order".
    for op in ("cancel_order", "place_order", "quote", "positions", "account"):
        must, nice = _OP_KEYWORDS[op]
        best, best_score = None, 0
        for name, desc in details:
            if name in used:
                continue
            lname = name.lower()
            if op in _ORDER_OPS and any(w in lname for w in _EXCLUDED_ASSET_WORDS):
                continue
            if op == "place_order" and "cancel" in lname:
                continue
            hay = f"{name} {desc}".lower()
            if not any(k in hay for k in must):
                continue
            score = sum(2 for k in must if k in lname) \
                + sum(1 for k in must if k in hay) \
                + sum(1 for k in nice if k in hay)
            if score > best_score or (score == best_score and best and len(name) < len(best)):
                best, best_score = name, score
        if best:
            mapping[op] = best
            used.add(best)
    return mapping


# -- tolerant parsing helpers (MCP tool results wrap content variously) ------
def _result_payload(res):
    """Pull a dict/list payload out of an MCP CallToolResult."""
    content = getattr(res, "structuredContent", None)
    if content is not None:
        return content
    blocks = getattr(res, "content", None) or []
    for block in blocks:
        text = getattr(block, "text", None)
        if text:
            import json
            try:
                return json.loads(text)
            except Exception:
                return {"_raw": text}
    return {}


def _extract_obj(res) -> dict:
    payload = _result_payload(res)
    if isinstance(payload, dict):
        return payload.get("result", payload) if "result" in payload else payload
    return {}


def _extract_rows(res) -> list[dict]:
    payload = _result_payload(res)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("positions", "results", "data", "items"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _extract_float(res, keys) -> float:
    data = _extract_obj(res)
    for k in keys:
        if k in data and data[k] is not None:
            try:
                return float(data[k])
            except (TypeError, ValueError):
                continue
    return 0.0
