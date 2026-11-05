"""The owner's KuCoin futures positions, read only, in the shape his Hyperliquid wallets are kept."""

import asyncio
import base64
import hashlib
import hmac
import json
import time

import httpx

from synth import analyst, kucoin, notify
from synth.config import Settings
from synth.store import Store

# KuCoin's documented example position (docs-new, Get Position List), isolated XBTUSDTM long
POS = {"symbol": "XBTUSDTM", "isOpen": True, "currentQty": 1, "avgEntryPrice": 120016.4, "markPrice": 120009.57,
       "markValue": 120.00957, "unrealisedPnl": -0.00683, "unrealisedRoePcnt": -0.0003, "realLeverage": 6.0,
       "leverage": 6.0, "liquidationPrice": 100475.86, "settleCurrency": "USDT", "isInverse": False,
       "positionSide": "LONG", "marginMode": "ISOLATED"}
# the public contract details, as KuCoin answered on 5 November 2026
XBT = {"symbol": "XBTUSDTM", "baseCurrency": "XBT", "multiplier": 0.001, "isInverse": False, "fundingFeeRate": 0.0001,
       "fundingRateGranularity": 28800000}
PEPE = {"symbol": "PEPEUSDTM", "baseCurrency": "PEPE", "multiplier": 520000.0, "isInverse": False,
        "fundingFeeRate": -0.0002, "fundingRateGranularity": 28800000}


def test_requests_are_signed_as_kucoin_documents():
    h = kucoin.headers("key", "secret", "pass", "get", "/api/v1/account-overview?currency=USDT", "", "2",
                       now_ms=1_700_000_000_000)
    want = base64.b64encode(hmac.new(b"secret", b"1700000000000GET/api/v1/account-overview?currency=USDT",
                                     hashlib.sha256).digest()).decode()
    assert h["KC-API-SIGN"] == want and h["KC-API-TIMESTAMP"] == "1700000000000"
    assert h["KC-API-PASSPHRASE"] == base64.b64encode(hmac.new(b"secret", b"pass", hashlib.sha256).digest()).decode()
    assert h["KC-API-KEY"] == "key" and h["KC-API-KEY-VERSION"] == "2"


def test_kucoin_coins_take_the_agents_names_and_units():
    known = {"BTC", "SOL", "kPEPE", "kBONK"}
    assert kucoin.coin_name("XBT", known) == ("BTC", 1.0)
    assert kucoin.coin_name("SOL", known) == ("SOL", 1.0)
    assert kucoin.coin_name("PEPE", known) == ("kPEPE", 1000.0)          # KuCoin per coin, Hyperliquid per 1,000
    assert kucoin.coin_name("1000BONK", known) == ("kBONK", 1.0)        # both per 1,000
    assert kucoin.coin_name("WIF", known) == ("WIF", 1.0)               # not on Hyperliquid: its own name


def test_positions_parse_into_the_agents_shape():
    short = {"symbol": "PEPEUSDTM", "isOpen": True, "currentQty": -10, "avgEntryPrice": 0.0000110,
             "markPrice": 0.0000100, "markValue": -52.0, "unrealisedPnl": 5.2, "realLeverage": 3,
             "liquidationPrice": 0.0000140, "settleCurrency": "USDT"}
    got = dict(kucoin.parse([POS, short, {**POS, "currentQty": 0}], {"XBTUSDTM": XBT, "PEPEUSDTM": PEPE},
                            {"BTC", "kPEPE"}))
    b = got["BTC"]
    assert (b["side"], b["size"], b["entry_px"], b["notional"], b["lev"], b["liq"]) == \
        ("long", 0.001, 120016.4, 120.01, 6.0, 100475.86)
    assert abs(b["funding_apr_pct"] - 10.95) < 0.06         # 0.01% every 8 hours, a year's worth
    p = got["kPEPE"]
    assert p["side"] == "short" and p["notional"] == 52.0
    assert round(p["entry_px"], 6) == 0.011 and round(p["mark"], 6) == 0.01   # in Hyperliquid's per-1,000 units
    assert round(p["size"], 3) == 5200.0                    # 10 lots of 520,000 PEPE: 5,200 kPEPE
    assert p["funding_apr_pct"] == -21.9 and len(got) == 2   # the closed one is left out


def _transport(refuse_version: str | None = None, seen: list | None = None):
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append((req.url.raw_path.decode(), req.headers.get("KC-API-KEY-VERSION")))
        if req.headers.get("KC-API-KEY-VERSION") == refuse_version:
            return httpx.Response(401, json={"code": "400003", "msg": "KC-API-KEY not exists"})
        path = req.url.raw_path.decode()
        if path == "/api/v1/positions":
            data = [POS]
        elif path.startswith("/api/v1/account-overview"):
            data = {"accountEquity": 400.5 if "USDT" in path else 20.0, "currency": "USDT"}
        elif path == "/api/v1/contracts/XBTUSDTM":
            data = XBT
        else:
            return httpx.Response(404, json={"code": "404000", "msg": "not found"})
        return httpx.Response(200, json={"code": "200000", "data": data})
    return httpx.MockTransport(handler)


def test_the_account_reads_and_finds_the_key_version():
    seen = []

    async def go():
        async with httpx.AsyncClient(transport=_transport(refuse_version="2", seen=seen)) as http:
            kc = kucoin.KuCoin("k", "s", "p", http=http)
            acct = await kc.account({"BTC"})
            return acct, kc.versions
    acct, versions = asyncio.run(go())
    assert acct["venue"] == "KuCoin" and acct["equity"] == 420.5 and set(acct["positions"]) == {"BTC"}
    assert versions == ["3"]                                # refused as version 2, accepted as 3, then remembered
    assert seen[0] == ("/api/v1/positions", "2") and seen[1] == ("/api/v1/positions", "3")
    assert all(v == "3" for p, v in seen[2:] if not p.startswith("/api/v1/contracts"))
    assert kucoin.accounts({**acct, "extra": {"BTC": {"side": "short"}}})["kucoin:2"]["positions"] == \
        {"BTC": {"side": "short"}}


def _agent(tmp_path, monkeypatch):
    from synth import service
    sent = []

    async def fake(token, chat_id, text, buttons=None):
        sent.append(text)
        return {"message_id": 1}

    monkeypatch.setattr(notify, "telegram", fake)
    s = Settings(db_path=str(tmp_path / "t.db"), telegram_token="t", telegram_chat_id="1", kucoin_key="k",
                 kucoin_secret="s", kucoin_passphrase="p")
    agent = service.Agent(s, Store(s.db_path))
    agent.positions.enabled = False
    return agent, sent


def test_his_kucoin_positions_reach_mine_and_survive_an_outage(tmp_path, monkeypatch):
    agent, _ = _agent(tmp_path, monkeypatch)
    now = time.time()
    agent.store.put_snapshot("hl_ctx", {"ts": now, "c": {"BTC": {"px": 120000.0}}})

    async def ok(self, known):
        return {"venue": "KuCoin", "equity": 400.0,
                "positions": dict(kucoin.parse([POS], {"XBTUSDTM": XBT}, known))}

    monkeypatch.setattr(kucoin.KuCoin, "account", ok)
    a = analyst.Analyst(agent, "claude-opus-5-5", client=None, quiet="")
    asyncio.run(a.read_owner(now))
    own = agent.store.get_snapshot("owner_positions")
    assert set(own["wallets"]) == {"kucoin"} and "kucoin_error" not in own
    assert agent.sources["kucoin"]["fails"] == 0
    assert agent.positions.against_his("BTC", "short").startswith("\nThis goes against your BTC long")
    txt = analyst.mine_text(agent, now)
    assert "BTC long" in txt and "KuCoin funding +11% a year (you pay)" in txt

    async def refused(self, known):
        raise kucoin.KuCoinError("HTTP 401, code 400003: KC-API-KEY not exists")

    monkeypatch.setattr(kucoin.KuCoin, "account", refused)
    asyncio.run(a.read_owner(now + 300))
    own = agent.store.get_snapshot("owner_positions")
    assert set(own["wallets"]) == {"kucoin"} and "400003" in own["kucoin_error"]    # the last reading stays
    assert agent.sources["kucoin"]["fails"] == 1
    agent.store.put_snapshot("owner_positions", {"ts": now, "wallets": {}, "kucoin_error": "HTTP 401"})
    assert "KuCoin could not be read: HTTP 401" in analyst.mine_text(agent, now)
    assert json.dumps(own)                                   # the snapshot stays plain data
