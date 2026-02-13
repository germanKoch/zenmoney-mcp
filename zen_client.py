from __future__ import annotations

import json
import time
import asyncio
import logging
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse, parse_qs

import httpx

from models import (
    Account,
    Budget,
    DiffResponse,
    Instrument,
    Merchant,
    Tag,
    Transaction,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.zenmoney.app"
CLIENT_ID = "g61164be3dd7521a6511ce97adc6bb"
CLIENT_SECRET = "b2828c65b7"
REDIRECT_PORT = 19876
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}"
TOKEN_FILE = Path(__file__).parent / ".token.json"
MIN_SYNC_INTERVAL = 60


class ZenMoneyError(Exception):
    pass


# --------------- OAuth helpers ---------------


def _save_token_data(data: dict) -> None:
    TOKEN_FILE.write_text(json.dumps(data))


def _load_token_data() -> dict | None:
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


class _CallbackHandler(BaseHTTPRequestHandler):
    """Handles the OAuth redirect callback."""

    auth_code: str | None = None

    def do_GET(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        code = qs.get("code", [None])[0]
        if code:
            _CallbackHandler.auth_code = code
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>OK!</h2>"
                b"<p>Token received. You can close this tab.</p>"
                b"</body></html>"
            )
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing code")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # silence request logs


async def _exchange_code(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/oauth2/token/",
            json={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uri": REDIRECT_URI,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code != 200:
            raise ZenMoneyError(f"Token exchange failed: {resp.text}")
        return resp.json()


async def _refresh_token(refresh_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/oauth2/token/",
            json={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code != 200:
            raise ZenMoneyError(f"Token refresh failed: {resp.text}")
        return resp.json()


async def obtain_token() -> str:
    """Run local OAuth flow: open browser, wait for callback, exchange code."""
    _CallbackHandler.auth_code = None
    server = HTTPServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler)
    thread = Thread(target=server.handle_request, daemon=True)
    thread.start()

    auth_url = (
        f"{BASE_URL}/oauth2/authorize/"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
    )
    logger.info("Opening browser for ZenMoney login...")
    webbrowser.open(auth_url)

    # Wait for callback (up to 120s)
    for _ in range(240):
        if _CallbackHandler.auth_code:
            break
        await asyncio.sleep(0.5)
    server.server_close()

    code = _CallbackHandler.auth_code
    if not code:
        raise ZenMoneyError("OAuth timed out — no auth code received")

    token_data = await _exchange_code(code)
    _save_token_data(token_data)
    return token_data["access_token"]


async def get_token() -> str:
    """Get a valid access token: from env, file (refresh if needed), or OAuth flow."""
    import os

    # 1. Env variable — always wins
    env_token = os.environ.get("ZENMONEY_TOKEN")
    if env_token:
        return env_token

    # 2. Saved token file
    data = _load_token_data()
    if data:
        access_token = data.get("access_token")
        refresh = data.get("refresh_token")

        # Try the saved access token first
        if access_token:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{BASE_URL}/v8/diff/",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={
                        "currentClientTimestamp": int(time.time()),
                        "serverTimestamp": int(time.time()),
                    },
                )
                if resp.status_code == 200:
                    return access_token

        # Access token expired — try refresh
        if refresh:
            try:
                new_data = await _refresh_token(refresh)
                _save_token_data(new_data)
                return new_data["access_token"]
            except ZenMoneyError:
                logger.warning("Refresh token failed, falling back to OAuth flow")

    # 3. Full OAuth flow
    return await obtain_token()


# --------------- API Client ---------------


class ZenMoneyClient:
    def __init__(self, token: str) -> None:
        self._token = token
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        self._server_ts = 0
        self._last_sync: float = 0

        # In-memory cache keyed by ID
        self.instruments: dict[int, Instrument] = {}
        self.accounts: dict[str, Account] = {}
        self.tags: dict[str, Tag] = {}
        self.merchants: dict[str, Merchant] = {}
        self.transactions: dict[str, Transaction] = {}
        self.budgets: dict[str, Budget] = {}

    async def close(self) -> None:
        await self._client.aclose()

    # ---------- sync ----------

    async def sync(self, *, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_sync) < MIN_SYNC_INTERVAL:
            return

        resp = await self._client.post(
            "/v8/diff/",
            json={
                "currentClientTimestamp": int(now),
                "serverTimestamp": self._server_ts,
            },
        )
        if resp.status_code != 200:
            raise ZenMoneyError(f"Sync failed ({resp.status_code}): {resp.text}")

        diff = DiffResponse.model_validate(resp.json())
        self._server_ts = diff.serverTimestamp
        self._last_sync = now

        for i in diff.instrument:
            self.instruments[i.id] = i
        for a in diff.account:
            self.accounts[a.id] = a
        for t in diff.tag:
            self.tags[t.id] = t
        for m in diff.merchant:
            self.merchants[m.id] = m
        for tr in diff.transaction:
            if tr.deleted:
                self.transactions.pop(tr.id, None)
            else:
                self.transactions[tr.id] = tr
        for b in diff.budget:
            key = f"{b.tag}:{b.date}"
            self.budgets[key] = b
        for d in diff.deletion:
            if d.object == "transaction":
                self.transactions.pop(d.id, None)
            elif d.object == "account":
                self.accounts.pop(d.id, None)
            elif d.object == "tag":
                self.tags.pop(d.id, None)
            elif d.object == "merchant":
                self.merchants.pop(d.id, None)

    # ---------- mutations ----------

    async def _push_diff(self, **kwargs: list) -> DiffResponse:
        now = int(time.time())
        body: dict = {"currentClientTimestamp": now, "serverTimestamp": self._server_ts}
        body.update(kwargs)
        resp = await self._client.post("/v8/diff/", json=body)
        if resp.status_code != 200:
            raise ZenMoneyError(f"Push failed ({resp.status_code}): {resp.text}")
        diff = DiffResponse.model_validate(resp.json())
        self._server_ts = diff.serverTimestamp
        return diff

    async def create_transaction(self, data: dict) -> Transaction:
        import uuid

        tr_id = str(uuid.uuid4())
        now = int(time.time())
        record = {
            "id": tr_id,
            "changed": now,
            "created": now,
            **data,
        }
        diff = await self._push_diff(transaction=[record])
        for tr in diff.transaction:
            self.transactions[tr.id] = tr
            if tr.id == tr_id:
                return tr
        return Transaction.model_validate(record)

    async def update_transaction(self, tr_id: str, updates: dict) -> Transaction:
        existing = self.transactions.get(tr_id)
        if not existing:
            raise ZenMoneyError(f"Transaction {tr_id} not found in cache")
        record = existing.model_dump()
        record.update(updates)
        record["changed"] = int(time.time())
        diff = await self._push_diff(transaction=[record])
        for tr in diff.transaction:
            self.transactions[tr.id] = tr
            if tr.id == tr_id:
                return tr
        return Transaction.model_validate(record)

    async def delete_transaction(self, tr_id: str) -> None:
        existing = self.transactions.get(tr_id)
        if not existing:
            raise ZenMoneyError(f"Transaction {tr_id} not found in cache")
        record = existing.model_dump()
        record["deleted"] = True
        record["changed"] = int(time.time())
        await self._push_diff(transaction=[record])
        self.transactions.pop(tr_id, None)

    # ---------- suggest ----------

    async def suggest(self, payee: str) -> list[dict]:
        resp = await self._client.post(
            "/v8/suggest/",
            json={"payee": payee},
        )
        if resp.status_code != 200:
            raise ZenMoneyError(f"Suggest failed ({resp.status_code}): {resp.text}")
        return resp.json()
