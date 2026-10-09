import asyncio

import httpx
import respx

from synth.sources import reflex

BASE = "https://reflex.test"

POSITIONING = {
    "wallets": 300,
    "updated_at": 1.0,
    "rows": [{
        "coin": "PUMP", "mark": 0.005,
        "long": {"n": 3, "usd": 900_000, "wallets": 3, "median_entry": 0.0049, "in_profit": 2},
        "short": {"n": 0, "usd": 0, "wallets": 0, "median_entry": None, "in_profit": 0},
        "opens_24h": {"long": 2, "short": 0}, "day_ago": {"long": 1, "short": 0},
        "convergence": {"side": "long", "n": 2, "ts": 1.0},
        "engine": {"4h": {"signal": "LIGHT_LONG", "regime": "MARKUP", "zscore": 0.4, "heat": 30, "price": 0.005},
                   "1d": {"signal": "WAIT", "regime": "ACCUM", "zscore": -0.2, "heat": 20, "price": 0.005}},
    }],
}


def routes(mock):
    mock.post(f"{BASE}/api/auth/login").respond(200, json={"ok": True, "token": "tok"})
    mock.get(f"{BASE}/api/notifications/convergence").respond(200, json={"items": [
        {"type": "CONVERGENCE", "symbol": "PUMP/USDT", "side": "long", "n": 2, "timestamp": 1.0,
         "detail": "2 profitable traders opened long", "dedup_key": "conv:PUMP:long:1:2"}]})
    mock.get(f"{BASE}/api/notifications/anomalies").respond(200, json={"anomalies": [], "count": 0})
    mock.get(f"{BASE}/api/notifications/market-setups").respond(200, json={"setups": []})
    mock.get(f"{BASE}/api/notifications/exhaustion-opportunities").respond(200, json={"opportunities": []})
    mock.get(f"{BASE}/api/market-pulse").respond(500)


@respx.mock
def test_login_then_fetch_normalises_and_tolerates_partial_failure():
    routes(respx)
    pos = respx.get(f"{BASE}/api/hyperlens/positioning").respond(200, json=POSITIONING)

    async def go():
        rc = reflex.ReflexClient(BASE, access_code="invite")
        try:
            return await reflex.fetch(rc)
        finally:
            await rc.aclose()

    snap = asyncio.run(go())
    assert pos.calls.last.request.headers["authorization"] == "Bearer tok"
    pump = snap["coins"]["PUMP"]
    assert pump["smart_lean"] == "long" and pump["signal_4h"] == "LIGHT_LONG" and pump["regime_1d"] == "ACCUM"
    assert snap["convergences"][0]["coin"] == "PUMP"
    assert "pulse" in snap["errors"] and snap["market"] == {}


@respx.mock
def test_expired_token_relogs_and_etag_304_reuses_body():
    respx.post(f"{BASE}/api/auth/login").respond(200, json={"token": "fresh"})
    route = respx.get(f"{BASE}/api/x")
    route.side_effect = [
        httpx.Response(401),
        httpx.Response(200, json={"a": 1}, headers={"etag": '"v1"'}),
        httpx.Response(304),
    ]

    async def go():
        rc = reflex.ReflexClient(BASE, access_code="c", token="old")
        a = await rc.get("/api/x")
        b = await rc.get("/api/x")
        await rc.aclose()
        return a, b

    a, b = asyncio.run(go())
    assert a == b == {"a": 1}
    assert route.calls[-1].request.headers["if-none-match"] == '"v1"'
    assert route.calls[1].request.headers["authorization"] == "Bearer fresh"
