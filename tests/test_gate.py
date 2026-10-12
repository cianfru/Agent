import asyncio
import importlib

import pytest
from eth_keys import keys
from fastapi.testclient import TestClient

from synth import gate as G

KEY = keys.PrivateKey(b"\x11" * 32)
ADDR = KEY.public_key.to_address().lower()
OTHER = keys.PrivateKey(b"\x22" * 32)
TOKEN = "0x" + "ab" * 20
SAFE = "0x" + "5a" * 20                                      # a smart-contract wallet


def sign(message, key=KEY):
    raw = key.sign_msg_hash(G.personal_hash(message)).to_bytes()
    return "0x" + (raw[:64] + bytes([raw[64] + 27])).hex()   # as wallets return it (v = 27/28)


class Chain:
    """balanceOf / decimals for TOKEN, and a Safe that accepts KEY's signatures (EIP-1271)."""

    def __init__(self, balances):
        self.balances, self.calls = balances, []

    async def __call__(self, method, params):
        self.calls.append(method)
        if method == "eth_getCode":
            return "0x6080" if params[0] == SAFE else "0x"
        to, data = params[0]["to"], params[0]["data"]
        if to == TOKEN and data == "0x313ce567":
            return hex(18)
        if to == TOKEN and data.startswith("0x70a08231"):
            who = "0x" + data[-40:]
            return hex(int(self.balances.get(who, 0) * 10 ** 18))
        if to == SAFE and data.startswith("0x1626ba7e"):
            return "0x1626ba7e" + "0" * 56
        return "0x"


def gate(balances, threshold=1000):
    g = G.Gate(rpc_url="https://rpc", token=TOKEN, threshold=threshold, chain_id=4663, secret=b"s" * 32)
    g._rpc = Chain(balances)
    return g


def test_off_until_configured():
    g = G.Gate(rpc_url="", token="", threshold=0, chain_id=1, secret=b"s")
    with pytest.raises(G.GateError) as e:
        g.challenge(ADDR, "desk.example", "https://desk.example")
    assert (e.value.status, e.value.code) == (503, "closed")


def test_sign_in_checks_the_signature_once_and_the_balance():
    g = gate({ADDR: 1500})
    ch = g.challenge(ADDR, "desk.example", "https://desk.example")
    msg = ch["message"]
    assert msg.startswith("desk.example wants you to sign in with your Ethereum account:\n" + G.checksum(ADDR))
    assert "Chain ID: 4663" in msg and f"Nonce: {ch['nonce']}" in msg and "Expiration Time:" in msg
    res = asyncio.run(g.verify(ch["nonce"], sign(msg)))
    assert res["address"] == ADDR and res["balance"] == 1500
    assert asyncio.run(g.session(res["cookie"])) == ADDR
    with pytest.raises(G.GateError) as e:                    # a nonce works once
        asyncio.run(g.verify(ch["nonce"], sign(msg)))
    assert e.value.status == 401


def test_wrong_signer_expired_or_too_few_tokens_are_refused():
    g = gate({ADDR: 1500})
    ch = g.challenge(ADDR, "d", "https://d")
    with pytest.raises(G.GateError) as e:
        asyncio.run(g.verify(ch["nonce"], sign(ch["message"], OTHER)))
    assert (e.value.status, e.value.code) == (401, "signature")
    ch = g.challenge(ADDR, "d", "https://d", now=1000.0)
    with pytest.raises(G.GateError) as e:
        asyncio.run(g.verify(ch["nonce"], sign(ch["message"]), now=1000.0 + G.CHALLENGE_S + 1))
    assert (e.value.status, e.value.code) == (401, "expired")
    poor = gate({ADDR: 999.5})
    ch = poor.challenge(ADDR, "d", "https://d")
    with pytest.raises(G.GateError) as e:
        asyncio.run(poor.verify(ch["nonce"], sign(ch["message"])))
    assert e.value.status == 403 and "1000" in str(e.value)
    assert (e.value.code, e.value.data["held"], e.value.data["threshold"]) == ("balance", 999.5, 1000)


def test_a_session_ends_when_the_tokens_go_or_the_cookie_is_forged():
    g = gate({ADDR: 1500})
    ch = g.challenge(ADDR, "d", "https://d", now=0.0 + 1)
    cookie = asyncio.run(g.verify(ch["nonce"], sign(ch["message"]), now=1.0))["cookie"]
    g._rpc.balances[ADDR] = 10                               # sold
    assert asyncio.run(g.session(cookie, now=2.0)) == ADDR   # read again only after BALANCE_TTL_S
    assert asyncio.run(g.session(cookie, now=2.0 + G.BALANCE_TTL_S)) is None
    body, _, mac = cookie.partition(".")
    assert asyncio.run(g.session(body + "." + "0" * 64, now=3.0)) is None
    assert asyncio.run(g.session(cookie, now=13 * 3600.0)) is None        # expired
    # the page is told why, so it can say so
    assert asyncio.run(g.check(None)) == (None, None)
    assert asyncio.run(g.check(body + "." + "0" * 64, now=3.0)) == (None, "invalid")
    assert asyncio.run(g.check(cookie, now=2.0 + G.BALANCE_TTL_S)) == (None, "balance")
    assert asyncio.run(g.check(cookie, now=13 * 3600.0)) == (None, "expired")


def test_a_smart_contract_wallet_signs_through_eip_1271():
    g = gate({SAFE: 5000})
    ch = g.challenge(SAFE, "d", "https://d")
    res = asyncio.run(g.verify(ch["nonce"], sign(ch["message"])))      # signed by its owner key
    assert res["address"] == SAFE and "eth_getCode" in g._rpc.calls


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTH_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("SYNTH_POLL_SECONDS", "0")
    monkeypatch.setenv("SYNTH_API_KEY", "k")
    for k, v in {"GATE_RPC_URL": "https://rpc", "GATE_TOKEN": TOKEN, "GATE_THRESHOLD": "1000",
                 "GATE_CHAIN_ID": "4663", "SYNTH_SESSION_SECRET": "x" * 32}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import synth.api
    mod = importlib.reload(synth.api)
    mod.gate._rpc = Chain({ADDR: 2000})
    yield mod, TestClient(mod.app, base_url="https://testserver")


def test_holders_read_the_desk_and_only_the_owner_runs_it(api):
    mod, c = api
    assert c.get("/desk").status_code == 401 and c.get("/auth/me").json()["role"] is None
    ch = c.post("/auth/challenge", json={"address": ADDR}).json()
    assert ch["message"].startswith("testserver wants you to sign in")
    r = c.post("/auth/verify", json={"nonce": ch["nonce"], "signature": sign(ch["message"])})
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
    assert "samesite=strict" in r.headers["set-cookie"].lower() and "secure" in r.headers["set-cookie"].lower()
    me = c.get("/auth/me").json()
    assert (me["role"], me["address"], me["balance"]) == ("holder", ADDR, 2000)
    for path in ("/desk", "/picks", "/position-traders", "/groups", "/research", "/study"):
        assert c.get(path).status_code == 200, path
    for method, path, body in (("post", "/cycle", None), ("get", "/posts", None), ("post", "/ask", {"question": "x"}),
                               ("get", "/backup", None), ("post", "/posts/1/publish", None),
                               ("post", "/groups/1/follow", None), ("post", "/brief", None)):
        kw = {"json": body} if body else {}
        assert getattr(c, method)(path, **kw).status_code == 401, path            # the owner's only
    assert c.get("/posts", headers={"x-api-key": "k"}).status_code == 200
    c.post("/auth/logout")
    assert c.get("/desk").status_code == 401


def test_sign_in_is_throttled(api):
    mod, c = api
    codes = [c.post("/auth/challenge", json={"address": ADDR}).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[-1] == 429
    assert c.post("/auth/challenge", json={"address": ADDR}).json()["detail"]["code"] == "busy"
    # verifying has its own allowance, so ten people behind one address can still finish signing in
    r = c.post("/auth/verify", json={"nonce": "n" * 16, "signature": "0x" + "0" * 130})
    assert r.status_code == 401


def test_refusals_carry_a_code_for_the_page(api, monkeypatch):
    mod, c = api
    r = c.post("/auth/challenge", json={"address": "0x" + "z" * 40})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "address"
    mod.gate._rpc.balances[ADDR] = 12
    ch = c.post("/auth/challenge", json={"address": ADDR}).json()
    r = c.post("/auth/verify", json={"nonce": ch["nonce"], "signature": sign(ch["message"])})
    d = r.json()["detail"]
    assert r.status_code == 403 and (d["code"], d["held"], d["threshold"]) == ("balance", 12, 1000)
    ch = c.post("/auth/challenge", json={"address": ADDR}).json()
    r = c.post("/auth/verify", json={"nonce": ch["nonce"], "signature": sign(ch["message"], OTHER)})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "signature"


def test_me_names_the_token_and_why_a_session_ended(api, monkeypatch):
    monkeypatch.setenv("GATE_SYMBOL", "OUT")
    monkeypatch.setenv("GATE_BUY_URL", "javascript:alert(1)")
    import synth.api
    mod = importlib.reload(synth.api)
    mod.gate._rpc = Chain({ADDR: 2000})
    c = TestClient(mod.app, base_url="https://testserver")
    me = c.get("/auth/me").json()
    assert me["gate"]["symbol"] == "OUT" and me["gate"]["buy_url"] is None     # only https links are offered
    assert me["ended"] is None
    ch = c.post("/auth/challenge", json={"address": ADDR}).json()
    c.post("/auth/verify", json={"nonce": ch["nonce"], "signature": sign(ch["message"])})
    mod.gate._rpc.balances[ADDR] = 0
    mod.gate._balances.clear()                                                    # next read goes to the chain
    me = c.get("/auth/me").json()
    assert me["role"] is None and me["ended"] == "balance"


def test_the_design_system_is_served_and_allowed(api):
    mod, c = api
    for path in ("/static/ui.css", "/static/ui.js", "/r/receipts.json", "/health"):
        assert c.get(path).headers["x-content-type-options"] == "nosniff", path
    assert c.get("/static/ui.js").status_code == 200 and c.get("/static/ui.css").status_code == 200
    assert c.get("/static/dashboard.html").status_code == 404     # pages only with their policy
    csp = c.get("/").headers["content-security-policy"]
    assert "script-src 'self' 'sha256-" in csp
