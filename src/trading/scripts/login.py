"""
Zerodha login — automatic access-token refresh.

Flow
----
1. Start a tiny HTTP server on 127.0.0.1:<LOGIN_CALLBACK_PORT> (default 8080).
2. Open the Kite login URL in the default browser.
3. User logs in with their Zerodha credentials.
4. Zerodha redirects to http://127.0.0.1:<port>/?request_token=XXX&status=success
5. Server captures the request_token, exchanges it for an access_token.
6. Writes ZERODHA_ACCESS_TOKEN to .env (creates the key if absent, updates if present).

Usage
-----
    uv run python -m trading.scripts.login

Prerequisites
-------------
In the Zerodha developer console (https://developers.kite.trade/apps),
set the Redirect URL for your app to:

    http://127.0.0.1:8080/   (or whatever LOGIN_CALLBACK_PORT is set to)

The API key and secret are read from .env.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv


# Locate .env: walk up from this file until we find it (or fall back to cwd).
def _find_env() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate
    return Path.cwd() / ".env"


_ENV_PATH = _find_env()

load_dotenv(_ENV_PATH)

API_KEY = os.environ.get("ZERODHA_API_KEY", "")
API_SECRET = os.environ.get("ZERODHA_API_SECRET", "")

if not API_KEY or not API_SECRET:
    sys.exit("ERROR: ZERODHA_API_KEY and ZERODHA_API_SECRET must be set in .env")

_CALLBACK_HOST = "127.0.0.1"


def _callback_port() -> int:
    from trading.config.settings import get_settings

    return get_settings().login_callback_port


# Shared result — set by the HTTP handler, read by main thread
_request_token: str | None = None
_server_error: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    """Handles the single redirect from Zerodha after login."""

    def do_GET(self) -> None:
        global _request_token, _server_error

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        status = params.get("status", [""])[0]
        token = params.get("request_token", [""])[0]

        if status == "success" and token:
            _request_token = token
            _server_error = None  # clear any stale error from earlier redirects
            body = b"<h2>Login successful! You can close this tab.</h2>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            threading.Thread(target=self.server.shutdown).start()
        elif parsed.path in ("/favicon.ico",):
            # Ignore browser probes — don't log or respond with an error page
            self.send_response(204)
            self.end_headers()
        else:
            # A real Zerodha error redirect — log it but keep listening so the
            # user can retry without restarting the script.
            _server_error = params.get("message", ["Unknown error"])[0]
            print(f"\nZerodha returned an error: {_server_error} — please try logging in again.")
            body = f"<h2>Login failed: {_server_error} — go back and try again.</h2>".encode()
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


def _write_token_to_env(token: str, env_path: Path) -> None:
    """Update (or insert) ZERODHA_ACCESS_TOKEN in .env."""
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""

    pattern = re.compile(r"^ZERODHA_ACCESS_TOKEN=.*$", re.MULTILINE)
    new_line = f"ZERODHA_ACCESS_TOKEN={token}"

    if pattern.search(text):
        text = pattern.sub(new_line, text)
    else:
        text = text.rstrip("\n") + f"\n{new_line}\n"

    env_path.write_text(text, encoding="utf-8")
    print(f"  Access token written to {env_path}")


def _exchange_and_save(client: object, request_token: str) -> None:
    from trading.broker.service.zerodha.kite_client import KiteClient

    assert isinstance(client, KiteClient)
    print("\nRequest token received. Exchanging for access token …")
    session = client.generate_session(request_token, API_SECRET)
    access_token: str = session["access_token"]
    _write_token_to_env(access_token, _ENV_PATH)
    print("\nLogin complete.")
    print(f"  User:         {session.get('user_name', 'unknown')}")
    print(f"  Login time:   {session.get('login_time', 'unknown')}")
    print(f"  Token prefix: {access_token[:8]}…")


def main() -> None:
    from trading.broker.service.zerodha.kite_client import KiteClient

    port = _callback_port()
    client = KiteClient(API_KEY)
    login_url = client.login_url()

    # Try to bind the callback server — fail fast if the port is occupied.
    try:
        server = HTTPServer((_CALLBACK_HOST, port), _CallbackHandler)
    except OSError:
        print(f"\nERROR: port {port} is already in use.")
        print("Stop the process holding it or set LOGIN_CALLBACK_PORT in .env to a free port.")
        print("\nAlternatively, open the login URL manually, complete login, then paste")
        print("the full redirect URL (or just the request_token) below when prompted.")
        print(f"\nLogin URL:\n  {login_url}\n")
        webbrowser.open(login_url)
        raw = input("Paste the redirect URL or request_token here: ").strip()
        # Accept either the full URL or just the bare token
        if raw.startswith("http"):
            params = parse_qs(urlparse(raw).query)
            request_token = params.get("request_token", [""])[0]
        else:
            request_token = raw
        if not request_token:
            sys.exit("ERROR: no request_token found in input")
        _exchange_and_save(client, request_token)
        return

    print(f"Starting local callback server on http://127.0.0.1:{port}/ …")
    print(f"\nOpening browser to Zerodha login:\n  {login_url}\n")
    webbrowser.open(login_url)

    print("Waiting for Zerodha redirect (log in with your credentials) …")
    server.serve_forever()  # blocks until _CallbackHandler shuts it down

    if not _request_token:
        sys.exit(f"ERROR: Login failed — {_server_error or 'no request_token received'}")

    _exchange_and_save(client, _request_token)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
