"""S5 as run on 12 October 2026 (docs/studies/S5-crowded-next-day.md).

Usage: python S5-run.py candles.json results.json
candles.json: {"candles": {COIN: [[t_ms, open, high, low, close], ...]}} from Hyperliquid daily candles
(synth.hyperliquid.HL.candles(coin, "1d", ...) for every perpetual in the meta universe, delisted included).
"""
import datetime as dt, json, math, sys
import numpy as np
D = json.load(open(sys.argv[1])); R = json.load(open("/home/user/Agent/synth/data/research.json"))
today0 = dt.datetime.now(dt.timezone.utc).date()
day = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date()
closes = {}
for c, rows in D["candles"].items():
    m = {day(r[0]): r[4] for r in rows if day(r[0]) < today0}   # drop today's unfinished candle
    if m: closes[c] = m
btc = closes["BTC"]
def ret(m, d, k):
    a, b = m.get(d), m.get(d + dt.timedelta(days=k))
    return None if a is None or b is None else b / a - 1
rng = np.random.default_rng(5)

def episodes(series, line):
    days = sorted(series); out = []
    for i, d in enumerate(days):
        if series[d] < line: continue
        prev = [d - dt.timedelta(days=j) for j in (1, 2, 3)]
        if all(p in series and series[p] < line for p in prev): out.append(d)
    return out

def stats(days, k):
    r = [ret(btc, d, k) for d in days]; r = [x for x in r if x is not None]
    return (len(r), float(np.median(r)) * 100 if r else None, 100 * sum(x > 0 for x in r) / len(r) if r else None, r)

def judge(series, line, label, verdict=True):
    days = sorted(series)
    out = {"line": line}
    base = {k: stats(days, k) for k in (1, 3)}
    ep = episodes(series, line)
    alld = [d for d in days if series[d] >= line]
    for k, gap in ((1, 0.5), (3, 1.0)):
        n, med, up, r = stats(ep, k); bn, bmed, bup, _ = base[k]
        an, amed, aup, _ = stats(alld, k)
        lo = hi = None
        if n:
            boots = np.median(rng.choice(np.array(r), size=(10000, n), replace=True), axis=1) * 100 - bmed
            lo, hi = np.percentile(boots, [5, 95])
        v = "context, not a signal"
        if verdict and n >= 15 and med is not None:
            if med <= bmed - gap and up <= 40 and hi < 0: v = "TENDS TO CORRECT"
            elif med >= bmed + gap and up >= 60 and lo > 0: v = "NO CORRECTION; THE MOVE CONTINUED"
        out[f"{k}d"] = dict(episodes=n, ep_median=med, ep_up=up, ci90=[lo, hi], base_n=bn, base_median=bmed, base_up=bup,
                            all_days=an, all_median=amed, all_up=aup, verdict=v if verdict else "description")
    out["episode_days"] = [str(d) for d in ep]
    return out

def show(name, o):
    print(f"\n{name} at {o['line']}%: episodes {o['1d']['episodes']} {o['episode_days'][:12]}{'…' if len(o['episode_days'])>12 else ''}")
    for k in ("1d", "3d"):
        x = o[k]; f = lambda v: "—" if v is None else f"{v:+.2f}"
        print(f"  {k}: episodes median {f(x['ep_median'])}% ({'—' if x['ep_up'] is None else round(x['ep_up'])}% up), 90% gap [{f(x['ci90'][0])}, {f(x['ci90'][1])}] "
              f"| every day at/above: n {x['all_days']} median {f(x['all_median'])}% ({'—' if x['all_up'] is None else round(x['all_up'])}% up) "
              f"| baseline n {x['base_n']} median {f(x['base_median'])}% ({round(x['base_up'])}% up) -> {x['verdict']}")

# A: the crowd's share long (S2 part B daily series)
# a stored row dated D is the 00:00 UTC reading of D, i.e. the close of D-1: it belongs to day D-1
A = {dt.date.fromisoformat(r[0]) - dt.timedelta(days=1): r[1] for r in next(s for s in R["studies"] if s["id"] == "S2")["series"]["rows"]}
A = {d: v for d, v in A.items() if d in btc}
print("A period", min(A), "to", max(A), len(A), "days")
resA = {line: judge(A, line, "A", verdict=(line == 80)) for line in (75, 80, 85)}
for line in (80, 75, 85): show("A crowd share long", resA[line])

# B: breadth, share of perps above their 50-day average
alld = sorted({d for m in closes.values() for d in m})
B, Bn = {}, {}
hist = {c: sorted(m.items()) for c, m in closes.items()}
idx = {c: {d: i for i, (d, _) in enumerate(h)} for c, h in hist.items()}
for d in alld:
    above = n = 0
    for c, h in hist.items():
        i = idx[c].get(d)
        if i is None or i < 49: continue
        win = [x for _, x in h[i - 49:i + 1]]
        n += 1; above += h[i][1] > sum(win) / 50
    if n >= 30: B[d] = 100 * above / n; Bn[d] = n
B = {d: v for d, v in B.items() if d in btc}
print("\nB period", min(B), "to", max(B), len(B), "days; coins per day", min(Bn.values()), "to", max(Bn.values()))
print("B days at/above 80:", sum(v >= 80 for v in B.values()), "| latest:", max(B), round(B[max(B)], 1), "%")
resB = {line: judge(B, line, "B", verdict=(line == 80)) for line in (75, 80, 85)}
for line in (80, 75, 85): show("B breadth (above 50-day average)", resB[line])

# basket next day after B episodes (description)
def basket(d):
    r = [math.log(m[d + dt.timedelta(days=1)] / m[d]) for m in closes.values() if d in m and d + dt.timedelta(days=1) in m]
    return float(np.mean(r)) * 100 if r else None
bk = [basket(d) for d in resB[80]["episode_days"] and [dt.date.fromisoformat(x) for x in resB[80]["episode_days"]]]
bk = [x for x in bk if x is not None]; allbk = [x for x in (basket(d) for d in B) if x is not None]
print(f"\nBasket next day after B 80% episodes: median {np.median(bk):+.2f}% ({100*np.mean(np.array(bk)>0):.0f}% up) vs all days {np.median(allbk):+.2f}% ({100*np.mean(np.array(allbk)>0):.0f}% up)")
json.dump({"A": resA, "B": resB, "B_series": {str(d): round(v, 1) for d, v in B.items()}, "B_coins": {str(d): n for d, n in Bn.items()},
           "basket_B80": {"median": float(np.median(bk)), "up": float(100*np.mean(np.array(bk)>0)), "n": len(bk),
                          "all_median": float(np.median(allbk)), "all_up": float(100*np.mean(np.array(allbk)>0))}},
          open(sys.argv[2], "w"), default=str)
