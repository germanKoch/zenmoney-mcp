from __future__ import annotations

import json
import os
import time
import asyncio
import logging
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse, parse_qs

import httpx

from .models import (
    Account,
    Budget,
    DiffResponse,
    Instrument,
    Merchant,
    Tag,
    Transaction,
    User,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.zenmoney.app"
CLIENT_ID = "g61164be3dd7521a6511ce97adc6bb"
CLIENT_SECRET = "b2828c65b7"
REDIRECT_PORT = 3000
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}"
TOKEN_FILE = Path(
    os.environ.get("ZENMONEY_TOKEN_FILE")
    or Path.home() / ".config" / "zenmoney-mcp" / "token.json"
)
MIN_SYNC_INTERVAL = 60


class ZenMoneyError(Exception):
    pass


# --------------- OAuth helpers ---------------


def _save_token_data(data: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
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


def _env_token_data() -> dict | None:
    """Build token data from environment variables, if any are set.

    Supported variables: ZENMONEY_ACCESS_TOKEN (alias: ZENMONEY_TOKEN),
    ZENMONEY_TOKEN_TYPE, ZENMONEY_EXPIRES_IN, ZENMONEY_REFRESH_TOKEN.
    """
    access = os.environ.get("ZENMONEY_ACCESS_TOKEN") or os.environ.get("ZENMONEY_TOKEN")
    refresh = os.environ.get("ZENMONEY_REFRESH_TOKEN")
    if not access and not refresh:
        return None
    data: dict = {}
    if access:
        data["access_token"] = access
    if refresh:
        data["refresh_token"] = refresh
    if token_type := os.environ.get("ZENMONEY_TOKEN_TYPE"):
        data["token_type"] = token_type
    if expires_in := os.environ.get("ZENMONEY_EXPIRES_IN"):
        try:
            data["expires_in"] = int(expires_in)
        except ValueError:
            logger.warning("ZENMONEY_EXPIRES_IN is not an integer, ignoring")
    return data


async def _is_token_valid(access_token: str) -> bool:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/v8/diff/",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "currentClientTimestamp": int(time.time()),
                "serverTimestamp": int(time.time()),
            },
        )
        return resp.status_code == 200


async def _token_from_data(data: dict) -> str | None:
    """Return a valid access token from token data, refreshing if needed."""
    access_token = data.get("access_token")
    if access_token and await _is_token_valid(access_token):
        return access_token

    # Access token missing or expired — try refresh
    refresh = data.get("refresh_token")
    if refresh:
        try:
            new_data = await _refresh_token(refresh)
            _save_token_data(new_data)
            return new_data["access_token"]
        except ZenMoneyError:
            logger.warning("Token refresh failed")
    return None


async def get_token() -> str:
    """Get a valid access token: from env, file (refresh if needed), or OAuth flow."""
    # 1. Env-provided token data — always tried first
    env_data = _env_token_data()
    if env_data:
        token = await _token_from_data(env_data)
        if token:
            return token
        logger.warning("Env-provided token is invalid or expired, trying saved token file")

    # 2. Saved token file
    data = _load_token_data()
    if data:
        token = await _token_from_data(data)
        if token:
            return token
        logger.warning("Saved token is invalid, falling back to OAuth flow")

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

        # Owner user id — required by the API on every pushed object
        self.user_id: int | None = None

        # In-memory cache keyed by ID
        self.users: dict[int, User] = {}
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

        for u in diff.user:
            self.users[u.id] = u
        # The account owner is the user without a parent; fall back to the first one.
        if self.users:
            owners = [u.id for u in self.users.values() if u.parent is None]
            self.user_id = owners[0] if owners else next(iter(self.users))

        for i in diff.instrument:
            self.instruments[i.id] = i
        for a in diff.account:
            self.accounts[a.id] = a

        # Incremental diffs may omit the user list — accounts carry the owner id too.
        if self.user_id is None:
            for a in self.accounts.values():
                owner = getattr(a, "user", None)
                if isinstance(owner, int):
                    self.user_id = owner
                    break
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
            raise ZenMoneyError(
                f"Push failed ({resp.status_code}): {resp.text}\nsent: {json.dumps(body, ensure_ascii=False)}"
            )
        diff = DiffResponse.model_validate(resp.json())
        self._server_ts = diff.serverTimestamp
        return diff

    async def _require_user_id(self) -> int:
        if self.user_id is None:
            await self.sync(force=True)
        if self.user_id is None:
            raise ZenMoneyError("Could not determine the ZenMoney user id — sync returned no user")
        return self.user_id

    def _transaction_template(self) -> dict:
        """A full Transaction skeleton: the API rejects partial objects, every
        property must be present — explicit nulls included.

        Only documented fields are sent: undocumented ones the server adds to
        its own responses (viewed, qrCode, incomeBankID, ...) make it answer 500
        when pushed back as null.
        """
        template: dict = {
            "deleted": False,
            "hold": None,
            "incomeInstrument": None,
            "incomeAccount": None,
            "income": 0,
            "outcomeInstrument": None,
            "outcomeAccount": None,
            "outcome": 0,
            "date": None,
            "tag": None,
            "merchant": None,
            "payee": None,
            "originalPayee": None,
            "comment": None,
            "mcc": None,
            "reminderMarker": None,
            "opIncome": None,
            "opIncomeInstrument": None,
            "opOutcome": None,
            "opOutcomeInstrument": None,
            "latitude": None,
            "longitude": None,
            # Undocumented, but the server rejects a push without them.
            "incomeBankID": None,
            "outcomeBankID": None,
            "qrCode": None,
        }
        return template

    async def create_transaction(self, data: dict) -> Transaction:
        import uuid

        tr_id = str(uuid.uuid4())
        now = int(time.time())
        record = {
            **self._transaction_template(),
            "id": tr_id,
            "user": await self._require_user_id(),
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
        if record.get("user") is None:
            record["user"] = await self._require_user_id()
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
        if record.get("user") is None:
            record["user"] = await self._require_user_id()
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
