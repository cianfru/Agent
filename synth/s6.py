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
3. Amendment 1 (declared 4 November 2026, before any of it was computed): the screen against Reflex's own ranking.
   Each day, among coins not too thin that carry both scores and a price 3 days later: the rank correlation of the
   screen's long score and of Reflex's priority score (its daily scan, 0-100, the default sort of its scanner) with the
   coin's next 3-day move up, on the same coins. Reflex's score ranks longs only, so the comparison is on longs. The
   difference (screen minus Reflex) per day, its mean and t over days. The screen ranks longs better when the mean
   difference is 0.02 or more with t of 2 or more over 60 days or more; Reflex's ranks them better at -0.02 or less
   with t of -2 or less; otherwise context, not a signal. Each ranking also gets question 1's bar on its own.
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
BAR = {"days": 60, "ic": 0.03, "t": 2.0, "bets": 60, "edge_pct": 1.0, "diff": 0.02}


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


def _mean_t(xs: list[float]) -> tuple[float, float | None]:
    m = statistics.fmean(xs)
    sd = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return m, (m / sd * math.sqrt(len(xs)) if sd else None)


def _summary(ics: list[float], word: str = "the screen ranks") -> dict:
    if not ics:
        return {"days": 0}
    m, t = _mean_t(ics)
    ok = len(ics) >= BAR["days"] and m >= BAR["ic"] and t is not None and t >= BAR["t"]
    return {"days": len(ics), "mean_ic": round(m, 3), "t": round(t, 2) if t is not None else None,
            "verdict": word if ok else "context, not a signal"}


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


def against_reflex(store) -> dict:
    """Question 3 (Amendment 1): the screen's long score and Reflex's priority score as rankings of the next 3-day
    move up, on the same days and the same coins."""
    rows = [r for r in store.screen_log() if r["coin"] != "*"]
    price = {(r["coin"], r["day"]): r.get("price") for r in rows}
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)
    ours: list[float] = []
    theirs: list[float] = []
    diffs: list[float] = []
    spread: list[float] = []
    for d, rs in sorted(by_day.items()):
        later = _day(d, H_DAYS)
        pts = []
        for r in rs:
            p0, p1, pr = r.get("price"), price.get((r["coin"], later)), r.get("priority_1d")
            ours_score = (r.get("long") or {}).get("score")
            if r.get("thin") or not p0 or not p1 or pr is None or ours_score is None:
                continue
            pts.append((float(ours_score), float(pr), p1 / p0 - 1))
        if len(pts) < MIN_COINS:
            continue
        fwd = [f for _, _, f in pts]
        a, b = spearman([o for o, _, _ in pts], fwd), spearman([t for _, t, _ in pts], fwd)
        if a is None or b is None:
            continue
        ours.append(a)
        theirs.append(b)
        diffs.append(a - b)
        ranked = sorted(pts, key=lambda p: p[1])
        k = max(1, len(ranked) // 5)
        spread.append(statistics.fmean(f for *_, f in ranked[-k:]) - statistics.fmean(f for *_, f in ranked[:k]))
    out = {"days": len(diffs), "screen": _summary(ours, "the screen ranks longs"),
           "reflex": _summary(theirs, "Reflex's priority ranks longs")}
    if spread:
        out["reflex"]["top_minus_bottom_pct"] = round(100 * statistics.fmean(spread), 2)
    verdict = "context, not a signal"
    if diffs:
        m, t = _mean_t(diffs)
        if len(diffs) >= BAR["days"] and t is not None:
            if m >= BAR["diff"] and t >= BAR["t"]:
                verdict = "the screen ranks longs better"
            elif m <= -BAR["diff"] and t <= -BAR["t"]:
                verdict = "Reflex's priority ranks longs better"
        out.update(diff=round(m, 3), diff_t=round(t, 2) if t is not None else None)
    out["verdict"] = verdict
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
    return {"screen": screen_ranking(store), "arms": arms(store), "reflex": against_reflex(store)}


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
    rf = rep.get("reflex") or {}
    if not rf.get("days"):
        third = f"S6, the screen against Reflex's priority (longs): no day scored yet ({BAR['days']} needed)."
    else:
        ic = lambda s: f"{s['mean_ic']:+.2f}" if s.get("days") else "-"  # noqa: E731
        t = rf["diff_t"] if rf.get("diff_t") is not None else "-"
        third = (f"S6, the screen against Reflex's priority (longs): {rf['days']} days of {BAR['days']} · screen "
                 f"{ic(rf['screen'])} · Reflex {ic(rf['reflex'])} · difference {rf['diff']:+.2f} (t {t}; bar "
                 f"+{BAR['diff']:.2f}, t 2) · {rf['verdict']}")
    return first + "\n" + second + "\n" + third
