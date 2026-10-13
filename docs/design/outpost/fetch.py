import json, pathlib, time, urllib.request
URL = "https://api.hyperliquid.xyz/info"
def post(body):
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "User-Agent": "outpost-proto"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())
now = int(time.time() * 1000)
start = now - 15 * 86400 * 1000
coins = ["BTC", "ETH", "SOL", "HYPE", "ZEC", "XRP", "DOGE", "SUI", "ENA", "PUMP", "AAVE", "LINK", "XPL", "NEAR", "FARTCOIN", "TAO"]
out = {"asof": now, "candles": {}, "funding": {}}
for c in coins:
    try:
        rows = post({"type": "candleSnapshot", "req": {"coin": c, "interval": "1h", "startTime": start, "endTime": now}})
        out["candles"][c] = [[r["t"] // 1000, float(r["c"])] for r in rows]
    except Exception as e:
        print("candle fail", c, e)
for c in ["BTC", "ETH"]:
    rows = post({"type": "fundingHistory", "coin": c, "startTime": start - 3600 * 1000})
    out["funding"][c] = [[r["time"] // 1000, float(r["fundingRate"])] for r in rows]
meta, ctx = post({"type": "metaAndAssetCtxs"})
out["ctx"] = {}
for u, x in zip(meta["universe"], ctx):
    if u["name"] in coins:
        out["ctx"][u["name"]] = {"oi": float(x["openInterest"]) * float(x["markPx"]), "funding": float(x["funding"]), "mark": float(x["markPx"])}
json.dump(out, open(pathlib.Path(__file__).parent / "hl.json", "w"))
print({k: len(v) for k, v in out["candles"].items()}, len(out["funding"]["BTC"]))
print(json.dumps(out["ctx"], indent=0)[:1500])
