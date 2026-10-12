"""The token gate: holders of the agent's token read the desk; the owner (API key) runs it.

A holder proves a wallet with Sign-In with Ethereum (EIP-4361). The server writes the exact message (its domain, the
chain, a single-use nonce, 10 minutes to sign), so nothing a browser sends is parsed; the signature is checked here
(EIP-191, or EIP-1271 on-chain for a smart-contract wallet such as a Safe), then the token balance is read on-chain
(ERC-20 `balanceOf`). At or above the threshold (a fixed number of tokens, not a dollar value) the holder gets a
session: a signed, HttpOnly cookie for SESSION_HOURS, whose balance is read again every BALANCE_TTL_S. Checked on the
server for every request, not a curtain in the browser.

Off until GATE_RPC_URL, GATE_TOKEN and GATE_THRESHOLD are set: until then only the owner's key opens the desk.
Not covered yet: smart wallets that are not deployed yet (ERC-6492) and WalletConnect (mobile wallets without an
in-app browser).
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass, field

import httpx
from eth_hash.auto import keccak
from eth_keys import keys
from eth_keys.exceptions import BadSignature

log = logging.getLogger(__name__)

CHALLENGE_S = 600
BALANCE_TTL_S = 600
MAX_PENDING = 2000
EIP1271_MAGIC = "0x1626ba7e"


class GateError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def personal_hash(message: str) -> bytes:
    """EIP-191: what a wallet signs for personal_sign."""
    raw = message.encode()
    return keccak(b"\x19Ethereum Signed Message:\n" + str(len(raw)).encode() + raw)


def recover(message: str, signature: str) -> str | None:
    """The address that signed `message` (lower case), or None for a signature that is not a plain key's."""
    try:
        sig = bytes.fromhex(signature.removeprefix("0x"))
        if len(sig) != 65:
            return None
        v = sig[64] - 27 if sig[64] >= 27 else sig[64]
        if v not in (0, 1):
            return None
        pub = keys.Signature(signature_bytes=sig[:64] + bytes([v])).recover_public_key_from_msg_hash(personal_hash(message))
        return pub.to_address().lower()
    except (ValueError, BadSignature):
        return None


def is_address(a: str) -> bool:
    return isinstance(a, str) and len(a) == 42 and a.startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in a[2:])


def checksum(a: str) -> str:
    """EIP-55, as wallets show and SIWE expects."""
    h = keccak(a.lower().removeprefix("0x").encode()).hex()
    return "0x" + "".join(c.upper() if int(h[i], 16) >= 8 else c for i, c in enumerate(a.lower().removeprefix("0x")))


def siwe_message(domain: str, address: str, uri: str, chain_id: int, nonce: str, issued: float, expires: float) -> str:
    def iso(t: float) -> str:
        return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (f"{domain} wants you to sign in with your Ethereum account:\n{checksum(address)}\n\n"
            f"Sign in to read the desk. This request does not trigger a transaction or cost any gas.\n\n"
            f"URI: {uri}\nVersion: 1\nChain ID: {chain_id}\nNonce: {nonce}\nIssued At: {iso(issued)}\n"
            f"Expiration Time: {iso(expires)}")


@dataclass
class Gate:
    rpc_url: str
    token: str
    threshold: float                      # whole tokens
    chain_id: int
    secret: bytes
    decimals: int | None = None
    session_hours: float = 12
    http: httpx.AsyncClient | None = None
    _pending: dict[str, dict] = field(default_factory=dict)
    _balances: dict[str, tuple[float, float]] = field(default_factory=dict)

    @property
    def on(self) -> bool:
        return bool(self.rpc_url and is_address(self.token) and self.threshold > 0)

    # ---- chain reads
    async def _rpc(self, method: str, params: list) -> str:
        own = self.http is None
        http = self.http or httpx.AsyncClient(timeout=10)
        try:
            r = await http.post(self.rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            r.raise_for_status()
            body = r.json()
        finally:
            if own:
                await http.aclose()
        if body.get("error"):
            raise GateError(502, f"chain read failed: {body['error'].get('message', body['error'])}")
        return body.get("result") or "0x"

    async def _call(self, to: str, data: str) -> str:
        return await self._rpc("eth_call", [{"to": to, "data": data}, "latest"])

    async def balance(self, address: str, now: float | None = None) -> float:
        """Whole tokens held, read again after BALANCE_TTL_S."""
        now = now or time.time()
        a = address.lower()
        got = self._balances.get(a)
        if got and now - got[1] < BALANCE_TTL_S:
            return got[0]
        if self.decimals is None:
            self.decimals = int(await self._call(self.token, "0x313ce567"), 16)     # decimals()
        raw = int(await self._call(self.token, "0x70a08231" + a[2:].rjust(64, "0")) or "0x0", 16)   # balanceOf
        whole = raw / 10 ** self.decimals
        self._balances[a] = (whole, now)
        return whole

    async def _contract_signed(self, address: str, message: str, signature: str) -> bool:
        """EIP-1271: a smart-contract wallet answers isValidSignature(hash, signature) with the magic value."""
        if (await self._rpc("eth_getCode", [address, "latest"])) in ("0x", "0x0", ""):
            return False
        sig = signature.removeprefix("0x")
        n = len(sig) // 2
        data = ("0x1626ba7e" + personal_hash(message).hex() + f"{64:064x}" + f"{n:064x}"
                + sig.ljust(((n + 31) // 32) * 64, "0"))
        res = await self._call(address, data)
        return res[:10].lower() == EIP1271_MAGIC

    # ---- sign-in
    def challenge(self, address: str, domain: str, uri: str, now: float | None = None) -> dict:
        if not self.on:
            raise GateError(503, "the token gate is not set up yet: the desk opens with the owner's key only")
        if not is_address(address):
            raise GateError(400, "not an address")
        now = now or time.time()
        self._pending = {k: v for k, v in self._pending.items() if v["exp"] > now}
        if len(self._pending) >= MAX_PENDING:
            raise GateError(429, "too many sign-ins at once; try again in a minute")
        nonce = secrets.token_hex(12)
        msg = siwe_message(domain, address, uri, self.chain_id, nonce, now, now + CHALLENGE_S)
        self._pending[nonce] = {"address": address.lower(), "message": msg, "exp": now + CHALLENGE_S}
        return {"nonce": nonce, "message": msg}

    async def verify(self, nonce: str, signature: str, now: float | None = None) -> dict:
        """Check the signature on a challenge (once) and the balance; returns {address, balance, cookie, max_age}."""
        now = now or time.time()
        ch = self._pending.pop(nonce, None)              # single use, whatever the outcome
        if not ch or ch["exp"] < now:
            raise GateError(401, "this sign-in request expired; start again")
        address = ch["address"]
        signer = recover(ch["message"], signature)
        if signer != address and not await self._contract_signed(address, ch["message"], signature):
            raise GateError(401, "the signature is not from this wallet")
        held = await self.balance(address, now)
        if held < self.threshold:
            raise GateError(403, f"holds {held:g} tokens; the desk opens from {self.threshold:g}")
        exp = now + self.session_hours * 3600
        return {"address": address, "balance": held, "cookie": self._sign({"a": address, "exp": exp}),
                "max_age": int(exp - now)}

    async def session(self, cookie: str | None, now: float | None = None) -> str | None:
        """The holder behind a session cookie, while it is valid and the wallet still holds enough; else None."""
        now = now or time.time()
        data = self._open(cookie or "")
        if not data or data.get("exp", 0) < now or not self.on:
            return None
        try:
            if await self.balance(data["a"], now) < self.threshold:
                return None
        except (httpx.HTTPError, GateError, ValueError) as e:
            log.warning("balance read failed for %s: %s", data["a"], e)
            got = self._balances.get(data["a"])          # a chain hiccup keeps a recent good read
            if not got or got[0] < self.threshold or now - got[1] > 4 * BALANCE_TTL_S:
                return None
        return data["a"]

    # ---- cookies
    def _sign(self, data: dict) -> str:
        body = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")
        mac = hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{mac}"

    def _open(self, cookie: str) -> dict | None:
        body, _, mac = cookie.partition(".")
        if not body or not hmac.compare_digest(mac.encode(), hmac.new(self.secret, body.encode(), hashlib.sha256)
                                               .hexdigest().encode()):
            return None
        try:
            return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except ValueError:
            return None


def from_settings(s) -> Gate:
    secret = s.session_secret.encode() if s.session_secret else secrets.token_bytes(32)
    if not s.session_secret and s.gate_token:
        log.warning("SYNTH_SESSION_SECRET is not set: holders sign in again after every restart")
    return Gate(rpc_url=s.gate_rpc_url, token=s.gate_token, threshold=s.gate_threshold, chain_id=s.gate_chain_id,
                secret=secret, decimals=s.gate_decimals)
