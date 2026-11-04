"""Study S6 (declared 4 November 2026, docs/studies/S6-screen-and-the-read.md): does the screen rank, and does the read
add anything over the code and over chance? Computed from what the agent keeps, never before its declared bar is met
is anything called more than context.

1. The screen's ranking: each day in the screen log, among coins that are not too thin, the rank correlation
   (Spearman) of each side's score with the coin's next 3-day move that way. A move beyond the market would rank the
   coins the same way on one day, so no market series is needed. The mean over days with t = mean / sd x sqrt(days);
   the daily z-score alone is reported beside the short side. The screen ranks when the mean is 0.03 or more with t of
   2 or more over 60 days or more.
2. The read against the code and against chance: the 3-day move beyond the market of the read's setups, the code's
   top candidates and the random controls, each in separate bets, averaged per day. The read adds when its mean beats
   the screen's by a point or more with the lower end of the difference's 95% interval above zero (days resampled),
   over 60 bets each; the same test, screen against random.
"""

from __future__ import annotations

import calendar
import math
import random
import statistics
import time

from . import journal

H_DAYS = 3
MIN_COINS = 8          # a day ranks only with this many coins scored and priced 3 days later
BAR = {"days": 60, "ic": 0.03, "t": 2.0, "bets": 60, "edge_pct": 1.0}


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2
        i = j + 1
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation, ties at their average rank. None when either side does not vary."""
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def _day(d: str, plus: int) -> str:
    t = calendar.timegm(time.strptime(d, "%Y-%m-%d")) + plus * 86400
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def _summary(ics: list[float]) -> dict:
    if not ics:
        return {"days": 0}
    m = statistics.fmean(ics)
    sd = statistics.stdev(ics) if len(ics) > 1 else 0.0
    t = m / sd * math.sqrt(len(ics)) if sd else None
    ok = len(ics) >= BAR["days"] and m >= BAR["ic"] and t is not None and t >= BAR["t"]
    return {"days": len(ics), "mean_ic": round(m, 3), "t": round(t, 2) if t is not None else None,
            "verdict": "the screen ranks" if ok else "context, not a signal"}


def screen_ranking(store) -> dict:
    """Question 1, from the screen log."""
    rows = [r for r in store.screen_log() if r["coin"] != "*"]
    price = {(r["coin"], r["day"]): r.get("price") for r in rows}
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)
    ic: dict[str, list[float]] = {"long": [], "short": [], "z_short": []}
    spread: dict[str, list[float]] = {"long": [], "short": []}
    fam: dict[str, list[float]] = {}
    for d, rs in sorted(by_day.items()):
        later = _day(d, H_DAYS)
        pts = []
        for r in rs:
            p0, p1 = r.get("price"), price.get((r["coin"], later))
            if r.get("thin") or not p0 or not p1:
                continue
            pts.append((r, p1 / p0 - 1))
        if len(pts) < MIN_COINS:
            continue
        for side, sign in (("long", 1), ("short", -1)):
            v = spearman([r[side]["score"] for r, _ in pts], [sign * f for _, f in pts])
            if v is not None:
                ic[side].append(v)
            ranked = sorted(pts, key=lambda x: x[0][side]["score"])
            k = max(1, len(ranked) // 5)                   # the top fifth against the bottom fifth, that way
            spread[side].append(sign * (statistics.fmean(f for _, f in ranked[-k:])
                                        - statistics.fmean(f for _, f in ranked[:k])))
            for name in ("price", "positioning", "engine"):
                v = spearman([1.0 if name in (r[side].get("lined_up") or []) else 0.0 for r, _ in pts],
                             [sign * f for _, f in pts])
                if v is not None:
                    fam.setdefault(f"{side}_{name}", []).append(v)
        zs = [(r["z_1d"], -f) for r, f in pts if r.get("z_1d") is not None]
        if len(zs) >= MIN_COINS:
            v = spearman([a for a, _ in zs], [b for _, b in zs])
            if v is not None:
                ic["z_short"].append(v)
    out = {k: _summary(v) for k, v in ic.items()}
    for side in ("long", "short"):
        if spread[side]:
            out[side]["top_minus_bottom_pct"] = round(100 * statistics.fmean(spread[side]), 2)
    out["families"] = {k: round(statistics.fmean(v), 3) for k, v in fam.items() if v}   # description only
    return out


def _arm(picks: list[dict]) -> tuple[int, dict[str, list[float]]]:
    by_day: dict[str, list[float]] = {}
    n = 0
    for p in journal.first_bets(picks):
        x = journal.excess(p, "3d")
        if x is None:
            continue
        n += 1
        by_day.setdefault(time.strftime("%Y-%m-%d", time.gmtime(p["ts"])), []).append(x)
    return n, by_day


def _diff(a: dict[str, list[float]], b: dict[str, list[float]], reps: int = 2000, seed: int = 6) -> tuple:
    """Mean of day means, a minus b, and the lower end of its 95% interval (each arm's days resampled)."""
    da = [statistics.fmean(v) for v in a.values() if v]
    db = [statistics.fmean(v) for v in b.values() if v]
    if len(da) < 2 or len(db) < 2:
        return None, None
    rng = random.Random(seed)
    diffs = sorted(statistics.fmean(rng.choice(da) for _ in da) - statistics.fmean(rng.choice(db) for _ in db)
                   for _ in range(reps))
    return statistics.fmean(da) - statistics.fmean(db), diffs[int(0.025 * reps)]


def arms(store, since: float = journal.S5_SETUPS_FROM) -> dict:
    """Question 2: the read's setups, the code's top candidates and random coins, beyond the market at 3 days."""
    got = {o: _arm([p for p in store.picks(limit=1_000_000, origin=o) if p["ts"] >= since])
           for o in ("read", "screen", "random")}
    out: dict = {o: {"bets": n, "mean_pct": round(100 * statistics.fmean(x for v in d.values() for x in v), 2)
                     if n else None} for o, (n, d) in got.items()}
    for a, b in (("read", "screen"), ("screen", "random"), ("read", "random")):
        diff, low = _diff(got[a][1], got[b][1])
        ok = (diff is not None and got[a][0] >= BAR["bets"] and got[b][0] >= BAR["bets"]
              and 100 * diff >= BAR["edge_pct"] and low is not None and low > 0)
        out[f"{a}_vs_{b}"] = {"diff_pct": round(100 * diff, 2) if diff is not None else None,
                              "low_pct": round(100 * low, 2) if low is not None else None,
                              "verdict": "adds" if ok else "context, not a signal"}
    return out


def report(store) -> dict:
    return {"screen": screen_ranking(store), "arms": arms(store)}


def text(rep: dict) -> str:
    """Two lines for /record: how far each question is from its bar."""
    sc = rep["screen"]
    lo, sh = sc["long"], sc["short"]
    if not lo.get("days") and not sh.get("days"):
        first = f"S6, does the screen rank: no day scored yet ({BAR['days']} needed)."
    else:
        part = lambda s, w: f"{w} {s['mean_ic']:+.2f} (t {s['t'] if s['t'] is not None else '-'})" if s.get("days") \
            else f"{w} -"   # noqa: E731
        first = (f"S6, does the screen rank: {max(lo.get('days', 0), sh.get('days', 0))} days of {BAR['days']} · "
                 f"{part(lo, 'longs')} · {part(sh, 'shorts')} (bar +0.03, t 2) · {lo.get('verdict', 'context')}")
    a = rep["arms"]
    second = (f"S6, the read against the code and chance: {a['read']['bets']} setups, {a['screen']['bets']} code picks, "
              f"{a['random']['bets']} random ({BAR['bets']} each needed)")
    rs = a["read_vs_screen"]
    if rs["diff_pct"] is not None:
        second += f" · read beyond the code {rs['diff_pct']:+.1f} points, at worst {rs['low_pct']:+.1f}"
    second += f" · {rs['verdict']}"
    return first + "\n" + second
