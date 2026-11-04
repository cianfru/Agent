"""The journal's record: how each kind of logged pick did, over every pick logged, not only the latest page.

Each kind answers its own study at its own horizon: Outrider's views (S5, 3 days), tracked traders' conviction bets
(S3, 30 days) and their other new positions, profitable traders opening together (S1), smart-money alerts and desk
posts. Returns are stored already turned to the pick's side, so a pick is right when its return is above zero.

Two guards against the market's own direction (when everything falls, shorts look right whatever the picking):
- longs and shorts are kept apart;
- each pick is also scored against the market (synth/market.py): it beats the market when its move its way is better
  than the basket's move its way over the same days. Reported beside the raw result; the studies' bars are unchanged.

`reads` says how Outrider's reads went (run, sent, held back, failed, with or without views), so a thin record can be
told apart from a broken one.
"""

from __future__ import annotations

import calendar
import math
import random
import statistics
import time

H5 = ("1d", "3d", "7d", "14d", "30d")
H3 = ("1d", "3d", "7d")
# S5 Amendment 2 (docs/studies/S5-outrider-reads.md): from 3 November 2026 a read's views are setups; the views before
# it are reported apart. S5's declared bar: 60 setups scored at 3 days, 62% right, median 3-day move +1% their way.
S5_SETUPS_FROM = calendar.timegm((2026, 11, 3, 0, 0, 0))
S5_BAR = {"n": 60, "right_pct": 62, "median_pct": 1.0}
# S5 Amendment 3 (adopted 4 November 2026, before any setup had a 3-day result): the verdict is judged on separate bets
# (one coin and side logged again within 3 days is one bet, scored from its first entry), on the 3-day move beyond the
# market: 60 bets, 62% beating the market, a median of +1% beyond it, and the lower end of the 95% interval of the mean
# move beyond it above zero (bets averaged per day, days resampled). The raw share right is shown beside, as context.
S3_LATE_FROM = calendar.timegm((2026, 11, 4, 0, 0, 0))     # S3 amendment: upgrades after the open logged on their own
KINDS = [  # key, label, study, horizons, the horizon its verdict uses, which picks
    ("read", "Outrider's setups (since 3 Nov)", "S5", H3, "3d",
     lambda p: p["origin"] == "read" and p["ts"] >= S5_SETUPS_FROM),
    ("read_before", "Outrider's views before 3 Nov", "S5, before Amendment 2", H3, "3d",
     lambda p: p["origin"] == "read" and p["ts"] < S5_SETUPS_FROM),
    # matched controls logged beside each read and never sent (S6): the code's top two candidates, and a
    # random liquid coin on each view's side
    ("screen", "The code's top candidates (shadow, never sent)", None, H3, "3d", lambda p: p["origin"] == "screen"),
    ("random", "Random liquid coins, same side (shadow control)", None, H3, "3d", lambda p: p["origin"] == "random"),
    ("conviction", "Tracked traders' conviction bets (2x usual size)", "S3", H5, "30d",
     lambda p: p["origin"] == "position" and (p.get("conviction") or 0) >= 2
     and not (p.get("state") or {}).get("upgraded_late")),
    ("conviction_late", "Conviction bets upgraded after the open, logged at the open (before 4 Nov; apart)",
     "S3, before its amendment", H5, "30d",
     lambda p: p["origin"] == "position" and (p.get("conviction") or 0) >= 2
     and (p.get("state") or {}).get("upgraded_late")),
    ("position", "Tracked traders' other new positions", None, H5, "30d",
     lambda p: p["origin"] == "position" and (p.get("conviction") or 0) < 2),
    ("group", "Profitable traders opening together", "S1", H3, "7d", lambda p: p["origin"] == "group"),
    ("alert", "Smart-money alerts", None, H3, "3d", lambda p: p["origin"] == "alert"),
    ("post", "Desk posts", None, H3, "7d", lambda p: p["origin"] == "post"),
]


def _stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0, "right": 0, "right_pct": None, "median_pct": None}
    right = sum(v > 0 for v in vals)
    med = statistics.median(vals)
    return {"n": len(vals), "right": right, "right_pct": round(100 * right / len(vals)),
            "median_pct": round(100 * med, 2), "median": med}         # median unrounded: the verdict reads it


def excess(p: dict, h: str) -> float | None:
    """The pick's move its way less the market's move its way over the same days."""
    ret, mkt = p.get(f"ret_{h}"), p.get(f"mkt_{h}")
    if ret is None or mkt is None:
        return None
    return ret - (mkt if p["direction"] == "long" else -mkt)


def verdict(a: dict, bar: dict = S5_BAR) -> str:
    """S5's verdict for a 3-day block: Useful only when all three declared conditions hold; else context."""
    return "meets the declared bar" if meets(a["n"], a["right"], a.get("median"), bar) else "context, not a signal"


def meets(n: int, right: int, median: float | None, bar: dict = S5_BAR) -> bool:
    """The declared bar, on unrounded figures (61.7% right is under a 62% bar, however it rounds)."""
    return n >= bar["n"] and 100 * right >= bar["right_pct"] * n and median is not None and \
        100 * median >= bar["median_pct"]


def first_bets(picks: list[dict], window: float = 3 * 86400) -> list[dict]:
    """The first pick of each separate bet: one coin and side logged again within `window` of the one before is the
    same bet, scored from its first entry."""
    out, last = [], {}
    for p in sorted(picks, key=lambda p: p["ts"]):
        k = (p["coin"], p["direction"])
        if k not in last or p["ts"] - last[k] > window:
            out.append(p)
        last[k] = p["ts"]
    return out


def day_low(by_day: dict[str, list[float]], reps: int = 2000, seed: int = 5) -> float | None:
    """The lower end of the 95% interval of the mean, the values averaged per day first and the days resampled
    (a day-block bootstrap): bets on the same day move together and are not independent evidence."""
    days = [statistics.fmean(v) for v in by_day.values() if v]
    if len(days) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choice(days) for _ in days) for _ in range(reps))
    return means[int(0.025 * reps)]


def s5(picks: list[dict], h: str = "3d", bar: dict = S5_BAR) -> dict:
    """S5's verdict under Amendment 3: separate bets, their move beyond the market, the declared bar and the
    interval. The raw share right is kept beside it as context."""
    bets = [p for p in first_bets(picks) if p.get(f"ret_{h}") is not None]
    xs = [(p, x) for p in bets if (x := excess(p, h)) is not None]
    by_day: dict[str, list[float]] = {}
    for p, x in xs:
        by_day.setdefault(time.strftime("%Y-%m-%d", time.gmtime(p["ts"])), []).append(x)
    n = len(xs)
    beat = sum(x > 0 for _, x in xs)
    med = statistics.median(x for _, x in xs) if xs else None
    low = day_low(by_day)
    ok = (n >= bar["n"] and 100 * beat >= bar["right_pct"] * n and med is not None and 100 * med >= bar["median_pct"]
          and low is not None and low > 0)
    return {"bets": n, "scored_bets_raw": len(bets), "beat": beat, "beat_pct": round(100 * beat / n) if n else None,
            "median_excess_pct": round(100 * med, 2) if med is not None else None,
            "mean_excess_pct": round(100 * statistics.fmean(x for _, x in xs), 2) if xs else None,
            "mean_low_pct": round(100 * low, 2) if low is not None else None, "days": len(by_day),
            "raw_right": sum(p[f"ret_{h}"] > 0 for p in bets), "bar": bar,
            "verdict": "meets the declared bar" if ok else "context, not a signal"}


def s5_text(v: dict, label: str = "Setups since 3 Nov (S5)") -> str:
    """One line against the bar, in plain words."""
    bar = v["bar"]
    line = f"{label}: {v['bets']} separate bets scored at 3 days, {bar['n']} needed"
    if v["bets"]:
        line += (f" · beat the market {v['beat']} of {v['bets']} ({v['beat_pct']}%, bar {bar['right_pct']}%)"
                 f" · median {v['median_excess_pct']:+.1f}% beyond it (bar +{bar['median_pct']:g}%)")
        if v.get("mean_low_pct") is not None:
            line += f" · mean {v['mean_excess_pct']:+.1f}%, at worst {v['mean_low_pct']:+.1f}% (95%)"
    line += f" · {v['verdict']}"
    if v["scored_bets_raw"]:
        line += f". Raw: {v['raw_right']} of {v['scored_bets_raw']} right"
    return line


def wilson(k: int, n: int, z: float = 1.96) -> tuple[int, int] | None:
    """The 95% interval for a share right of k in n (Wilson's), in percent."""
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    w = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(100 * max(0.0, c - w)), round(100 * min(1.0, c + w))


def episodes(picks: list[dict], window: float = 3 * 86400) -> int:
    """Picks on one coin and side logged within `window` of the one before count as one episode: the same bet repeated
    on following days is not new evidence."""
    n, last = 0, {}
    for p in sorted(picks, key=lambda p: p["ts"]):
        k = (p["coin"], p["direction"])
        if k not in last or p["ts"] - last[k] > window:
            n += 1
        last[k] = p["ts"]
    return n


def beta_excess(p: dict, h: str) -> float | None:
    """The pick's move its way less what its beta to BTC explains: ret - side x beta x BTC's move."""
    path = (p.get("state") or {}).get(f"path_{h}") or {}
    beta, btc, ret = (p.get("state") or {}).get("beta_btc"), path.get("btc_pct"), p.get(f"ret_{h}")
    if beta is None or btc is None or ret is None:
        return None
    return ret - (1 if p["direction"] == "long" else -1) * beta * btc / 100


def block(picks: list[dict], h: str) -> dict:
    got = [p for p in picks if p.get(f"ret_{h}") is not None]
    vs = [x for p in got if (x := excess(p, h)) is not None]
    out = {**_stats([p[f"ret_{h}"] for p in got]), "vs_market": _stats(vs)}
    if got:
        out["interval"] = wilson(out["right"], out["n"])
        out["days"] = len({time.strftime("%Y-%m-%d", time.gmtime(p["ts"])) for p in got})
        out["episodes"] = episodes(got)
        paths = [p["state"][f"path_{h}"] for p in got if (p.get("state") or {}).get(f"path_{h}")]
        if paths:
            out["net_mean_pct"] = round(statistics.fmean(x["net_pct"] for x in paths), 2)
            out["mae_median_pct"] = round(statistics.median(x["mae_pct"] for x in paths), 2)
            named = [x for x in paths if "off_hit" in x]
            if named:
                out["off_hit"] = {"n": len(named), "hit": sum(1 for x in named if x["off_hit"])}
        be = [x for p in got if (x := beta_excess(p, h)) is not None]
        if be:
            out["beta_adjusted_median_pct"] = round(100 * statistics.median(be), 2)
    return out


def record(store) -> list[dict]:
    picks = store.picks(limit=1_000_000)
    out = []
    for key, label, study, horizons, main, pick in KINDS:
        sel = [p for p in picks if pick(p)]
        if not sel:
            continue
        row = {"origin": key, "label": label, "study": study, "main": main, "logged": len(sel),
               "since": time.strftime("%d %b", time.gmtime(min(p["ts"] for p in sel))),
               "longs": sum(p["direction"] == "long" for p in sel), "shorts": sum(p["direction"] == "short" for p in sel),
               "h": {}}
        for h in horizons:
            row["h"][h] = {**block(sel, h), "long": block([p for p in sel if p["direction"] == "long"], h),
                           "short": block([p for p in sel if p["direction"] == "short"], h)}
        # the study's horizon once anything has reached it, else the longest that has
        row["shown"] = main if row["h"][main]["n"] else next(
            (h for h in reversed(horizons) if row["h"][h]["n"]), main)
        out.append(row)
    return out


def reads(store, now: float | None = None, days: float = 7) -> dict:
    """Outrider's reads over the last `days`: how many ran, went out, were held back or failed, and their views."""
    now = now or time.time()
    bs = [b["body"] for b in store.briefs_since("read", now - days * 86400)]
    failed = [b for b in bs if b.get("error")]
    sent = [b for b in bs if b.get("sent")]
    reasons: dict[str, int] = {}
    for b in bs:
        reasons[b.get("reason") or "?"] = reasons.get(b.get("reason") or "?", 0) + 1
    return {"days": days, "run": len(bs), "sent": len(sent), "held_back": len(bs) - len(sent) - len(failed),
            "failed": len(failed), "by_reason": reasons,
            "sent_without_view": sum(1 for b in sent if not b.get("views")),
            "views_given": sum(len(b.get("views") or []) for b in sent),
            "views_logged": sum(len(b.get("logged") or []) for b in sent),
            "last_error": failed[-1]["error"] if failed else None}


def reads_text(r: dict) -> str:
    if not r["run"]:
        return f"Outrider's reads, {r['days']:g} days: none ran."
    why = ", ".join(f"{n} {k}" for k, n in sorted(r["by_reason"].items(), key=lambda kv: -kv[1]))
    line = (f"Outrider's reads, {r['days']:g} days: {r['run']} ran ({why}) · {r['sent']} sent · {r['held_back']} held "
            f"back · {r['failed']} failed · {r['sent_without_view']} sent with no view · {r['views_logged']} views logged")
    if r["views_given"] > r["views_logged"]:
        line += f" ({r['views_given'] - r['views_logged']} repeats or without a price)"
    if r["last_error"]:
        line += f". Last failure: {r['last_error'][:160]}"
    return line


def _h(r: dict, h: str) -> str:
    s = r["h"][h]
    if not s["n"]:
        return f"{h}: none scored yet"
    line = f"{h}: {s['right']} of {s['n']} right ({s['right_pct']}%), median {s['median_pct']:+.1f}%"
    v = s["vs_market"]
    if v["n"]:
        line += f"; beat the market {v['right']} of {v['n']} ({v['right_pct']}%), median {v['median_pct']:+.1f}%"
    return line


def text(rows: list[dict], rd: dict | None = None, extra: str = "", s5v: dict | None = None) -> str:
    """For Telegram: S5's verdict line, one block per kind at the horizon its study uses, longs and shorts apart, then
    the reads and `extra` (S6)."""
    lines = [s5_text(s5v)] if s5v else []
    for r in rows:
        lines.append(f"{r['label']}: {r['logged']} since {r['since']} ({r['longs']} long, {r['shorts']} short)")
        lines.append("  " + _h(r, r["shown"]))
        s = r["h"][r["shown"]]
        if s["n"]:
            lo, sh = s["long"], s["short"]
            lines.append(f"  longs {lo['right']}/{lo['n']} right, shorts {sh['right']}/{sh['n']} right")
            more = [f"95% interval for right {s['interval'][0]}-{s['interval'][1]}%",
                    f"{s['episodes']} separate bet{'s' if s['episodes'] != 1 else ''} over {s['days']} "
                    f"day{'s' if s['days'] != 1 else ''}"]
            if s.get("net_mean_pct") is not None:
                more.append(f"after fees and funding {s['net_mean_pct']:+.1f}% on average")
            if s.get("off_hit"):
                more.append(f"touched its off level {s['off_hit']['hit']} of {s['off_hit']['n']}")
            if s.get("beta_adjusted_median_pct") is not None:
                more.append(f"beyond its beta to BTC {s['beta_adjusted_median_pct']:+.1f}% (median)")
            lines.append("  " + " · ".join(more))
    if not lines:
        lines.append("Nothing logged yet.")
    if rd:
        lines += ["", reads_text(rd)]
    if extra:
        lines += ["", extra]
    return "\n".join(lines)


def full_text(store) -> str:
    """/record: S5's verdict, every kind, the reads, and S6's two questions."""
    from . import s6
    picks = [p for p in store.picks(limit=1_000_000, origin="read") if p["ts"] >= S5_SETUPS_FROM]
    return text(record(store), reads(store), s6.text(s6.report(store)), s5(picks))
