"""Durable OAuth for the Robinhood MCP -- built for unattended, long-running use.

The problem this solves: OAuth access tokens are short-lived (minutes to an hour).
A loop meant to run for *months* on an always-on server can't rely on a token
pasted in by hand -- it would die at the first expiry. So this adapter becomes
its **own** OAuth client:

* You authenticate **once** on any device with a browser (`trading-agent login`).
* Tokens -- including the long-lived **refresh token** -- are saved to a portable
  JSON file (:class:`FileTokenStorage`).
* You copy that file to the always-on host. From then on the ``mcp`` SDK's OAuth
  provider **auto-refreshes** the access token on every expiry, using the refresh
  token, with no human present.

This is deliberately independent of Claude Code's own OAuth session -- the
adapter holds its own registration and tokens, so nothing breaks if Claude Code
isn't installed on the server.

⚠️  Honest limit on "months": refresh tokens don't necessarily live forever.
If Robinhood caps refresh-token lifetime or requires periodic re-consent, the
loop will eventually need one more `trading-agent login`. How long that is, is
Robinhood's policy, not something this code controls. The loop logs loudly and
keeps your positions safe (it just stops trading) if auth ever lapses.
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

DEFAULT_TOKEN_PATH = os.getenv(
    "ROBINHOOD_TOKEN_PATH",
    str(pathlib.Path.home() / ".trading-agent" / "robinhood_oauth.json"),
)
DEFAULT_CALLBACK_PORT = int(os.getenv("ROBINHOOD_OAUTH_PORT", "8765"))


class FileTokenStorage:
    """Persists OAuth tokens + client registration to a 0600 JSON file.

    Implements the ``mcp.client.auth.TokenStorage`` protocol. The file is the
    single portable artifact you move to your server; guard it like a password
    (it contains the refresh token)."""

    def __init__(self, path: str | None = None):
        self.path = pathlib.Path(path or DEFAULT_TOKEN_PATH)

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)  # refresh token lives here
        except OSError:
            pass  # best-effort on platforms without chmod

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        data = self._read().get("tokens")
        return OAuthToken(**data) if data else None

    async def set_tokens(self, tokens) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(exclude_none=True)
        self._write(data)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        data = self._read().get("client_info")
        return OAuthClientInformationFull(**data) if data else None

    async def set_client_info(self, info) -> None:
        data = self._read()
        data["client_info"] = info.model_dump(exclude_none=True, mode="json")
        self._write(data)

    def has_tokens(self) -> bool:
        return bool(self._read().get("tokens"))


def _wait_for_callback(port: int, timeout: float = 300.0) -> tuple[str, str | None]:
    """Run a one-shot local server to catch the OAuth redirect; return (code, state)."""
    captured: dict[str, str | None] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            captured["code"] = (params.get("code") or [None])[0]
            captured["state"] = (params.get("state") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>trading-agent: authorized.</h2>"
                             b"<p>You can close this tab and return to the terminal.</p>")
            done.set()

        def log_message(self, *_args):
            pass  # keep the terminal quiet

    server = HTTPServer(("localhost", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not done.wait(timeout):
            raise TimeoutError("Timed out waiting for the OAuth redirect.")
    finally:
        server.shutdown()
    if not captured.get("code"):
        raise RuntimeError("OAuth redirect did not include an authorization code.")
    return captured["code"], captured.get("state")


def build_oauth_provider(server_url: str, storage: FileTokenStorage,
                         port: int = DEFAULT_CALLBACK_PORT, interactive: bool = True):
    """Construct an mcp OAuthClientProvider that stores/refreshes via ``storage``.

    ``interactive=True`` opens a browser for first-time consent. On a headless
    server set it False -- if a valid refresh token already exists the SDK will
    use it silently; if not, it raises instead of trying to pop a browser.
    """
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    redirect_uri = f"http://localhost:{port}/callback"
    metadata = OAuthClientMetadata(
        client_name="trading-agent",
        redirect_uris=[redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope=os.getenv("ROBINHOOD_MCP_SCOPE") or None,
    )

    async def redirect_handler(auth_url: str) -> None:
        if not interactive:
            raise RuntimeError(
                "Robinhood authorization required but running non-interactively. "
                "Run `trading-agent login` on a device with a browser, then copy "
                f"{storage.path} to this host."
            )
        print("\nOpening your browser to authorize Robinhood. If it doesn't open, "
              "paste this URL:\n" + auth_url + "\n")
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

    async def callback_handler() -> tuple[str, str | None]:
        import anyio
        return await anyio.to_thread.run_sync(lambda: _wait_for_callback(port))

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
