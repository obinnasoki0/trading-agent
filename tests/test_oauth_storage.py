"""Tests for the durable OAuth token store.

These don't touch the network -- they exercise the persistence layer that lets
the loop refresh tokens unattended. The store must survive process restarts
(that's the whole point) and never crash on a missing/corrupt file.
"""

import asyncio
import os

import pytest

pytest.importorskip("mcp")  # OAuth storage needs the MCP SDK models

from mcp.shared.auth import OAuthToken  # noqa: E402

from trading_agent.brokers.robinhood_oauth import FileTokenStorage  # noqa: E402


def test_tokens_round_trip(tmp_path):
    store = FileTokenStorage(tmp_path / "tok.json")
    assert asyncio.run(store.get_tokens()) is None
    assert not store.has_tokens()

    tok = OAuthToken(access_token="a", token_type="Bearer",
                     refresh_token="r", expires_in=3600)
    asyncio.run(store.set_tokens(tok))

    # A fresh instance (simulating a restart) must read the same tokens back.
    reloaded = FileTokenStorage(tmp_path / "tok.json")
    got = asyncio.run(reloaded.get_tokens())
    assert got.access_token == "a"
    assert got.refresh_token == "r"
    assert reloaded.has_tokens()


def test_missing_file_is_safe(tmp_path):
    store = FileTokenStorage(tmp_path / "nope.json")
    assert asyncio.run(store.get_tokens()) is None
    assert asyncio.run(store.get_client_info()) is None


def test_corrupt_file_is_safe(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    store = FileTokenStorage(path)
    assert asyncio.run(store.get_tokens()) is None  # degrades, doesn't raise


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode check")
def test_token_file_is_not_world_readable(tmp_path):
    store = FileTokenStorage(tmp_path / "tok.json")
    asyncio.run(store.set_tokens(OAuthToken(access_token="a", token_type="Bearer")))
    mode = oct(os.stat(store.path).st_mode)[-3:]
    assert mode == "600"  # refresh token must not be readable by others
