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
map them in ``TOOL_MAP`` below. This adapter defaults to **dry-run** and will
not place real orders unless ``allow_live=True``.

Requires the official MCP SDK:  pip install "trading-agent[robinhood]"  (mcp>=1.0)

Auth (durable, for unattended runs -- see robinhood_oauth.py):
* `trading-agent login` once on a device with a browser -> tokens saved to a
  portable file that auto-refreshes. Copy that file to your always-on server.
* Or set ROBINHOOD_MCP_TOKEN to a static bearer token to override OAuth (short
  tests only -- it won't refresh and will expire).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from ..core.models import AccountState, Order, OrderStatus, Position, Side
from .base import Broker
from .robinhood_oauth import FileTokenStorage, build_oauth_provider

MCP_URL = os.getenv("ROBINHOOD_MCP_URL", "https://agent.robinhood.com/mcp/trading")

# Map our operations -> Robinhood MCP tool names.
# Confirmed against the live server (2026-07) via read-only calls to get_accounts,
# get_portfolio, get_equity_positions, and get_equity_quotes.
TOOL_MAP = {
    "account": "get_portfolio",
    "positions": "get_equity_positions",
    "quote": "get_equity_quotes",
    "place_order": "place_equity_order",
    "cancel_order": "cancel_equity_order",
}


class RobinhoodMCPBroker(Broker):
    name = "robinhood_mcp"
    is_live = True

    def __init__(self, allow_live: bool = False, dry_run: bool = True,
                 url: str | None = None, tool_map: dict | None = None,
                 account_number: str | None = None, token_path: str | None = None,
                 interactive: bool = False):
        self.allow_live = allow_live
        self.dry_run = dry_run
        self.url = url or MCP_URL
        self.tool_map = tool_map or dict(TOOL_MAP)
        # Static token (env) overrides OAuth -- handy for a short manual test.
        self._token = os.getenv("ROBINHOOD_MCP_TOKEN")
        self._account_number = account_number
        # Durable OAuth: tokens persist here and auto-refresh for unattended runs.
        self._storage = FileTokenStorage(token_path)
        self.interactive = interactive

    # -- MCP plumbing -----------------------------------------------------
    def _transport_auth(self) -> dict:
        """kwargs for streamablehttp_client: static bearer, or the OAuth provider
        that refreshes itself from the persisted refresh token."""
        if self._token:
            return {"headers": {"Authorization": f"Bearer {self._token}"}}
        provider = build_oauth_provider(self.url, self._storage, interactive=self.interactive)
        return {"auth": provider}

    @asynccontextmanager
    async def _open_session(self):
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError('Install the MCP SDK: pip install "trading-agent[robinhood]"') from exc

        async with streamablehttp_client(self.url, **self._transport_auth()) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def _call_async(self, tool: str, arguments: dict | None = None):
        async with self._open_session() as session:
            return await session.call_tool(tool, arguments or {})

    def _call(self, op: str, arguments: dict | None = None):
        tool = self.tool_map[op]
        return asyncio.run(self._call_async(tool, arguments))

    def login(self) -> list[str]:
        """Run the one-time interactive OAuth flow (opens a browser) and persist
        tokens so the loop can refresh them unattended afterward."""
        self.interactive = True
        self._token = None  # force the OAuth path even if an env token is set
        tools = self.list_tools()  # first connect triggers auth + token storage
        print(f"Authorized. Tokens saved to {self._storage.path}")
        return tools

    def _resolve_account_number(self) -> str:
        """Look up and cache the account_number to use for account-scoped calls.

        get_portfolio / get_equity_positions / place_equity_order / cancel_equity_order
        all require an account_number that this adapter is never handed directly, so we
        discover it once via get_accounts and prefer the agentic_allowed=true account
        (the only one this agent is permitted to act on)."""
        if self._account_number:
            return self._account_number
        res = asyncio.run(self._call_async("get_accounts"))
        accounts = _extract_obj(res).get("accounts", [])
        if not accounts:
            raise RuntimeError("get_accounts returned no Robinhood accounts.")
        agentic = [a for a in accounts if a.get("agentic_allowed")]
        chosen = (agentic or accounts)[0]
        self._account_number = chosen["account_number"]
        return self._account_number

    def list_tool_details(self) -> list[tuple[str, str]]:
        """Discover (name, description) for every tool the live MCP exposes."""
        async def _run():
            async with self._open_session() as session:
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
        res = self._call("quote", {"symbols": [symbol]})
        for row in _extract_rows(res):
            quote = row.get("quote") or row
            for key in ("last_trade_price", "last_non_reg_trade_price"):
                val = quote.get(key)
                if val is not None:
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        continue
        return 0.0

    def positions(self) -> dict[str, Position]:
        res = self._call("positions", {"account_number": self._resolve_account_number()})
        out: dict[str, Position] = {}
        for row in _extract_rows(res):
            sym = row.get("symbol")
            qty = float(row.get("quantity", 0) or 0)
            if sym and qty:
                out[sym] = Position(sym, qty, float(row.get("average_buy_price", 0) or 0))
        return out

    def account(self) -> AccountState:
        res = self._call("account", {"account_number": self._resolve_account_number()})
        data = _extract_obj(res)
        cash = float(data.get("cash", 0) or 0)
        equity = float(data.get("total_value", cash) or cash)
        return AccountState(cash=cash, equity=equity, positions=self.positions(), timestamp=datetime.now())

    def submit(self, order: Order) -> Order:
        if self.dry_run or not self.allow_live:
            order.status = OrderStatus.REJECTED
            order.broker_id = "dry-run"
            print(f"[DRY-RUN] would {order.side.value} {order.quantity:.4f} {order.symbol} "
                  f"via Robinhood MCP")
            return order
        try:
            res = self._call("place_order", {
                "account_number": self._resolve_account_number(),
                "symbol": order.symbol,
                "side": order.side.value,
                "quantity": str(round(order.quantity, 6)),
                "type": order.order_type.value,
                "ref_id": str(uuid.uuid4()),
                **({"limit_price": str(order.limit_price)} if order.limit_price else {}),
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
        self._call("cancel_order", {
            "account_number": self._resolve_account_number(),
            "order_id": broker_id,
        })


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

# quote/positions must also stay equity-scoped -- otherwise keyword-score ties
# (e.g. get_equity_quotes vs get_index_quotes) can resolve to the wrong asset
# class purely on the "shorter name wins" tiebreak.
_ASSET_SCOPED_OPS = {"quote", "positions"}
_NON_EQUITY_ASSET_WORDS = ("option", "crypto", "index")

# Tools that keyword-match an op's vocabulary only because their *description*
# cross-references the real tool (e.g. get_accounts says "route buying-power
# questions through get_portfolio", which falsely inflates its own "account"
# score) -- exclude them by name rather than trying to out-tune the scorer.
_OP_NAME_DENYLIST = {
    "account": {"get_accounts"},  # lists multiple accounts; not one account's balance
}


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
            if name in _OP_NAME_DENYLIST.get(op, ()):
                continue
            if op in _ORDER_OPS and any(w in lname for w in _EXCLUDED_ASSET_WORDS):
                continue
            if op in _ASSET_SCOPED_OPS and any(w in lname for w in _NON_EQUITY_ASSET_WORDS):
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


def _unwrap(payload):
    """Peel off a top-level 'data' or 'result' envelope, if the payload is wrapped in one.

    The live Robinhood MCP wraps every response as {"data": {...}, "guide": "..."};
    "result" is kept as an alternate envelope key for other MCP servers/tool shapes."""
    if isinstance(payload, dict):
        for key in ("data", "result"):
            inner = payload.get(key)
            if isinstance(inner, (dict, list)):
                return inner
    return payload


def _extract_obj(res) -> dict:
    payload = _unwrap(_result_payload(res))
    return payload if isinstance(payload, dict) else {}


def _extract_rows(res) -> list[dict]:
    payload = _unwrap(_result_payload(res))
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
