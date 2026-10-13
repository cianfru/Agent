"""Sample positioning for the Outpost prototype, built around real Hyperliquid prices (hl.json).
Prices and BTC/ETH funding are real; every position, trader, group and record entry is made up."""
import json, math, random, sys, bisect, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from synth.wallet_names import codename

R = random.Random(13)
HERE = pathlib.Path(__file__).parent
hl = json.load(open(HERE / "hl.json"))
C = hl["candles"]
T = [t for t, _ in C["BTC"]]
NOW = T[-1]
H = 3600

def px(coin, t):
    rows = C[coin]; ts = [r[0] for r in rows]
    i = max(0, min(len(rows) - 1, bisect.bisect_right(ts, t) - 1))
    return rows[i][1]

def at(day, hour):            # October 2026, UTC
    import datetime as dt
    return int(dt.datetime(2026, 10, day, hour, tzinfo=dt.timezone.utc).timestamp())

FLUSH = (at(8, 12), at(8, 17))

# ---- crowd share long (hourly): buys dips, trims rallies (S2), 71% a day ago, 78% now
btc = [p for _, p in C["BTC"]]
crowd = []
for i, t in enumerate(T):
    r24 = btc[i] / btc[max(0, i - 24)] - 1
    s = 72.5 - 140 * r24 + 1.6 * math.sin(i / 17) + R.gauss(0, 0.45)
    if t > FLUSH[0]:
        s += 6.5 * math.exp(-((t - FLUSH[1] - 4 * H) / (16 * H)) ** 2)
    crowd.append(s)
# pin the last day: 71 a day ago -> 78 now, smoothly
i24 = len(T) - 25
d0, d1 = 71.0 - crowd[i24], 78.2 - crowd[-1]
for k in range(i24, len(T)):
    w = (k - i24) / 24
    crowd[k] += d0 * (1 - w) + d1 * w
crowd = [round(min(92, max(55, s)), 2) for s in crowd]

# ---- open interest, all Hyperliquid perps the agent watches (sample history around today's real total)
oi_now = sum(v["oi"] for v in hl["ctx"].values())
oi = []
for t in T:
    if t < FLUSH[0]:
        base = 1.01 + 0.055 * (t - T[0]) / (FLUSH[0] - T[0])
    elif t < FLUSH[1]:
        base = 1.065 - 0.125 * (t - FLUSH[0]) / (FLUSH[1] - FLUSH[0])
    else:
        base = 0.94 + 0.065 * (t - FLUSH[1]) / (NOW - FLUSH[1])
    oi.append(round(oi_now * base * (1 + R.gauss(0, 0.0025))))
oi[-1] = round(oi_now)

# ---- traders
def addr():
    return "0x" + "".join(R.choice("0123456789abcdef") for _ in range(40))
traders = []
for i in range(14):
    a = addr()
    n = R.randint(12, 31); won = round(n * R.uniform(0.55, 0.86))
    traders.append({"address": a, "name": codename(a), "tier": "proven", "closed": n, "won": won,
                    "realized": R.randint(60, 900) * 1000, "per_loss": round(R.uniform(1.6, 4.8), 1),
                    "hold_days": R.randint(4, 16), "equity": R.randint(300, 6000) * 1000, "positions": []})
for i in range(3):
    a = addr()
    traders.append({"address": a, "name": codename(a), "tier": "riding", "closed": None, "won": None,
                    "realized": None, "open_profit": R.randint(150, 700) * 1000, "per_loss": None,
                    "hold_days": R.randint(6, 20), "equity": R.randint(500, 4000) * 1000, "positions": []})
traders[0].update(closed=17, won=14, per_loss=4.1, realized=842000)   # ZEC's best record

# positions: (coin, side, n, entry window start, end, $ range in K)
book = [
    ("ZEC", "long", 9, at(8, 16), at(13, 9), (180, 900)),
    ("HYPE", "short", 5, at(11, 10), at(12, 20), (150, 700)),
    ("HYPE", "long", 1, at(6, 3), at(6, 9), (80, 120)),
    ("NEAR", "long", 4, at(8, 18), at(9, 14), (120, 500)),
    ("BTC", "long", 6, at(4, 0), at(12, 0), (400, 2400)),
    ("BTC", "short", 3, at(6, 0), at(12, 0), (300, 1500)),
    ("ETH", "long", 4, at(5, 0), at(12, 0), (300, 1500)),
    ("ETH", "short", 4, at(7, 0), at(12, 0), (300, 1200)),
    ("SOL", "long", 2, at(9, 0), at(11, 0), (100, 400)),
    ("SOL", "short", 3, at(7, 0), at(12, 0), (150, 500)),
    ("PUMP", "short", 2, at(10, 0), at(12, 0), (60, 200)),
    ("TAO", "long", 3, at(8, 18), at(10, 0), (80, 300)),
    ("TAO", "short", 1, at(12, 0), at(12, 12), (60, 100)),
    ("XRP", "long", 2, at(5, 0), at(10, 0), (100, 300)),
    ("XRP", "short", 1, at(11, 0), at(12, 0), (80, 150)),
    ("SUI", "long", 2, at(9, 0), at(11, 0), (60, 200)),
    ("ENA", "short", 2, at(10, 0), at(12, 0), (60, 160)),
    ("AAVE", "long", 2, at(8, 18), at(11, 0), (80, 250)),
    ("XPL", "short", 2, at(7, 0), at(11, 0), (40, 120)),
    ("DOGE", "long", 1, at(9, 0), at(10, 0), (60, 120)),
    ("DOGE", "short", 1, at(11, 0), at(12, 0), (50, 100)),
    ("LINK", "long", 1, at(10, 0), at(11, 0), (50, 100)),
    ("FARTCOIN", "long", 1, at(9, 0), at(10, 0), (40, 80)),
]
used = {}
for coin, side, n, a, b, (lo, hi) in book:
    pool = [t for t in traders if all(p["coin"] != coin for p in t["positions"])]
    if coin == "ZEC":
        pool = [traders[0]] + [t for t in pool if t is not traders[0]]
    else:
        R.shuffle(pool)
    for t in pool[:n]:
        ts = R.randint(a, b) // 300 * 300
        size = R.randint(lo, hi) * 1000
        t["positions"].append({"coin": coin, "side": side, "entry_ts": ts, "entry": round(px(coin, ts), 6),
                               "usd": size, "conviction": size > hi * 1000 * 0.8})
# today's adds on the three
adds = {"ZEC": ("long", 2_140_000, 5), "NEAR": ("long", 1_210_000, 1), "HYPE": ("short", 860_000, 3)}

# ---- lenses per coin
crowd_share = {"BTC": 74, "ETH": 70, "SOL": 80, "HYPE": 76, "ZEC": 71, "XRP": 77, "DOGE": 84, "SUI": 79, "ENA": 81,
               "PUMP": 83, "AAVE": 69, "LINK": 72, "XPL": 64, "NEAR": 68, "FARTCOIN": 86, "TAO": 66}
big = {"BTC": (7, 4), "ETH": (5, 5), "SOL": (3, 4), "HYPE": (4, 4), "ZEC": (5, 1), "NEAR": (3, 0), "TAO": (2, 1),
       "XRP": (2, 2), "PUMP": (0, 2), "DOGE": (1, 1), "SUI": (1, 0), "ENA": (0, 1), "AAVE": (1, 0), "XPL": (0, 1),
       "LINK": (1, 0), "FARTCOIN": (0, 0)}
lenses = {}
for coin, ctx in hl["ctx"].items():
    heads = max(40, int(ctx["oi"] / 2.5e6))
    ln = round(heads * crowd_share[coin] / 100)
    pl = [p for t in traders for p in t["positions"] if p["coin"] == coin and p["side"] == "long"]
    ps = [p for t in traders for p in t["positions"] if p["coin"] == coin and p["side"] == "short"]
    bl, bs = big[coin]
    lenses[coin] = {"crowd": {"long_n": ln, "short_n": heads - ln},
                    "proven": {"long_n": len(pl), "short_n": len(ps), "long_usd": sum(p["usd"] for p in pl),
                               "short_usd": sum(p["usd"] for p in ps)},
                    "big": {"long_n": bl, "short_n": bs},
                    "oi": round(ctx["oi"]), "funding_apr": round(ctx["funding"] * 24 * 365 * 100, 1),
                    "added_24h": ({"side": adds[coin][0], "usd": adds[coin][1], "traders": adds[coin][2]}
                                  if coin in adds else None)}

# ---- groups (2+ proven traders opening the same side within 6h), tracked to close
def member(t, coin, side, ts, exit_ts=None):
    p0 = px(coin, ts)
    m = {"name": t["name"], "address": t["address"], "entry_ts": ts, "entry": round(p0, 6), "usd": R.randint(90, 600) * 1000}
    if exit_ts:
        m.update(exit_ts=exit_ts, exit=round(px(coin, exit_ts), 6))
    return m
G = []
ts = at(9, 2)
G.append({"id": 47, "coin": "ZEC", "side": "long", "ts": ts, "members": [member(traders[k], "ZEC", "long", ts + k * 2400) for k in (0, 3, 5, 8)]})
ts = at(11, 14)
G.append({"id": 46, "coin": "HYPE", "side": "short", "ts": ts, "members": [member(traders[k], "HYPE", "short", ts + k * 1800) for k in (2, 6, 9)]})
ts = at(8, 19)
G.append({"id": 45, "coin": "NEAR", "side": "long", "ts": ts, "members": [member(traders[4], "NEAR", "long", ts),
          member(traders[7], "NEAR", "long", ts + 5400, exit_ts=at(12, 4))]})
ts = at(7, 6)
G.append({"id": 44, "coin": "SOL", "side": "short", "ts": ts, "members": [member(traders[1], "SOL", "short", ts, exit_ts=at(9, 10)),
          member(traders[10], "SOL", "short", ts + 3000, exit_ts=at(10, 2))]})
ts = at(4, 21)
G.append({"id": 43, "coin": "PUMP", "side": "long", "ts": ts, "members": [member(traders[k], "PUMP", "long", ts + j * 2700, exit_ts=at(8, 15) + j * 3600) for j, k in enumerate((11, 12, 13))]})

# ---- the record: every logged call, scored at 1, 3, 7 days (sample)
coins = list(hl["ctx"])
record = []
t = at(1, 3)
pid = 212
while t < NOW - 6 * H:
    coin = R.choice(coins); side = R.choice(["long", "long", "short"])
    p0 = px(coin, t)
    row = {"id": pid, "ts": t, "coin": coin, "side": side, "entry": p0, "kind": R.choice(["conviction", "group", "build-up"])}
    for h, d in (("1d", 1), ("3d", 3), ("7d", 7)):
        if t + d * 86400 <= NOW:
            r = px(coin, t + d * 86400) / p0 - 1
            row[h] = round(100 * (r if side == "long" else -r), 2)
    row["hash"] = "%016x" % R.getrandbits(64)
    record.append(row)
    pid += 1
    t += R.randint(5, 16) * H
record.reverse()

out = {"asof": NOW, "flush": FLUSH, "times": T, "crowd": crowd, "oi": oi,
       "prices": {c: [p for _, p in rows] for c, rows in C.items()},
       "funding": {c: [[a, round(r * 24 * 365 * 100, 2)] for a, r in rows] for c, rows in hl["funding"].items()},
       "lenses": lenses, "traders": traders, "groups": G, "record": record,
       "studies": json.load(open(ROOT / "synth/data/research.json"))["studies"]}
for s in out["studies"]:
    s.pop("doc", None)
json.dump(out, open(HERE / "sample.json", "w"), separators=(",", ":"))
print(len(json.dumps(out, separators=(",", ":"))) // 1024, "KB; crowd now", crowd[-1], "24h ago", crowd[-25],
      "max", max(crowd), "oi now %.2fB" % (oi[-1] / 1e9), "records", len(record))
for c in ("ZEC", "HYPE", "NEAR"):
    print(c, lenses[c]["proven"], lenses[c]["crowd"])
