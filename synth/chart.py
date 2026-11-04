"""Charts for Telegram and the dashboard, drawn in code at no cost: a group's 48 hours, the live pulse, a setup on its
daily chart, the market map and the record against its bar.

Pure functions of their inputs; each returns PNG bytes. Figures are built with matplotlib's object API (no pyplot
state), so two renders in two threads never mix. Sized for a phone: 5 inches wide at 240 dpi (1,200 px, the long side
under Telegram's 1,280), no text under 8.5 points.
"""

from __future__ import annotations

import datetime as dt
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.patches  # noqa: E402,F401
import matplotlib.ticker  # noqa: E402,F401
from matplotlib.figure import Figure  # noqa: E402

GROUND, SURFACE, BORDER = "#0e0d0b", "#161412", "#3b352f"
TEXT, MUTED = "#f7f3ed", "#8f877c"
UP, DOWN, ACCENT = "#9fe6c7", "#f4968b", "#ff5e00"


def _fmt(x: float) -> str:
    return f"{x:,.0f}" if x >= 1000 else f"{x:.2f}" if x >= 1 else f"{x:.4g}"


def group_chart(coin: str, side: str, candles: list[list[float]], members: list[dict],
                all_losing: float | None, title: str) -> bytes:
    """candles: [[t_ms, o, h, l, c]]; members: [{entry_px, opened_ts, status}]."""
    fig = Figure(figsize=(8, 4.5), dpi=150)
    ax = fig.subplots()
    fig.patch.set_facecolor(GROUND)
    ax.set_facecolor(SURFACE)
    for s in ax.spines.values():
        s.set_color(BORDER)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.grid(color=BORDER, linewidth=0.5, alpha=0.6)

    side_col = DOWN if side == "short" else UP
    if candles:
        t = [dt.datetime.fromtimestamp(c[0] / 1000, dt.timezone.utc) for c in candles]
        close = [c[4] for c in candles]
        ax.plot(t, close, color=TEXT, linewidth=1.2)
        last = close[-1]
        ax.annotate(f"now {_fmt(last)}", (t[-1], last), xytext=(6, 0), textcoords="offset points",
                    color=TEXT, fontsize=8, va="center")

    entries = [m for m in members if m.get("entry_px")]
    if entries:
        lo, hi = min(m["entry_px"] for m in entries), max(m["entry_px"] for m in entries)
        ax.axhspan(lo, hi, color=side_col, alpha=0.10, linewidth=0)
        for m in entries:
            when = dt.datetime.fromtimestamp(m["opened_ts"], dt.timezone.utc)
            closed = m.get("status") in ("closed", "expired")
            ax.scatter([when], [m["entry_px"]], s=36, color=side_col if not closed else MUTED,
                       edgecolor=GROUND, linewidth=0.8, zorder=3, marker="v" if side == "short" else "^")
    if all_losing:
        ax.axhline(all_losing, color=ACCENT, linewidth=1, linestyle="--")
        word = "above" if side == "short" else "below"
        ax.annotate(f"all {side}s losing {word} {_fmt(all_losing)}", xy=(0.01, all_losing),
                    xycoords=("axes fraction", "data"), xytext=(0, 4 if side == "short" else -10),
                    textcoords="offset points", color=ACCENT, fontsize=8)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b %H:%M", tz=dt.timezone.utc))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=6))
    ax.set_title(title, color=TEXT, fontsize=10, loc="left")
    ax.text(1.0, 1.02, "UTC · Outrider", transform=ax.transAxes, ha="right", color=MUTED, fontsize=7)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=GROUND)
    return buf.getvalue()


def _usd(x: float) -> str:
    a = abs(x)
    s = f"${a / 1e6:.1f}M" if a >= 1e6 else f"${a / 1e3:.0f}K" if a >= 1e3 else f"${a:.0f}"
    return ("+" if x > 0 else "−" if x < 0 else "") + s


def _signed(n: int) -> str:
    return f"+{n}" if n > 0 else f"−{-n}" if n < 0 else "0"


def pulse_chart(points: list[dict], grid: dict, now: float, title: str = "Crowd pulse · profitable traders on Hyperliquid") -> bytes:
    """The live pulse for Telegram: share of profitable traders' positions that are long over 24h, and the coins
    with the most movement, cell by cell for the last 1h, 6h and 24h. Pure; returns PNG bytes."""
    coins = grid["coins"]
    rows_h = 0.42 * max(len(coins), 3) + 0.5
    height = 1.0 + 2.2 + rows_h + 0.55
    fig = Figure(figsize=(8, height), dpi=150)
    fig.patch.set_facecolor(GROUND)
    gs = fig.add_gridspec(2, 1, height_ratios=[2.2, rows_h], hspace=0.18,
                          left=0.07, right=0.97, top=1 - 1.0 / height, bottom=0.55 / height)
    when = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    top_y = 1 - 0.32 / height
    fig.text(0.07, top_y, title, color=TEXT, fontsize=12, fontweight="bold", va="center")
    t = (grid["windows"].get(1) or {}).get("totals") or {}
    if t.get("long_share") is not None:
        fig.text(0.07, top_y - 0.28 / height,
                 f"{t['long_n']} long · {t['short_n']} short · {t['long_share']}% of positions long",
                 color=MUTED, fontsize=9, va="center")
    fig.text(0.97, top_y, when.strftime("%d %b %H:%M UTC"), color=MUTED, fontsize=8, ha="right", va="center")

    # 1. the lean over 24h
    ax = fig.add_subplot(gs[0])
    ax.set_facecolor(SURFACE)
    for s in ax.spines.values():
        s.set_color(BORDER)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.grid(color=BORDER, linewidth=0.5, alpha=0.6)
    if points:
        ts = [dt.datetime.fromtimestamp(p["ts"], dt.timezone.utc) for p in points]
        ys = [p["long_share"] for p in points]
        lo, hi = min(ys), max(ys)
        pad = max(0.5, (hi - lo) * 0.25)
        ax.set_ylim(lo - pad, hi + pad)
        if lo - pad < 50 < hi + pad:
            ax.axhline(50, color=MUTED, linewidth=0.8, linestyle="--")
        ax.plot(ts, ys, color=TEXT, linewidth=1.6)
        ax.annotate(f"{ys[-1]:.1f}%", (ts[-1], ys[-1]), xytext=(-6, 10), textcoords="offset points",
                    color=TEXT, fontsize=8, ha="right", bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1})
        for h in (6, 1):
            x = dt.datetime.fromtimestamp(now - h * 3600, dt.timezone.utc)
            if ts[0] <= x:
                ax.axvline(x, color=BORDER, linewidth=1, linestyle=":")
                ax.text(x, 1.0, f" {h}h", transform=ax.get_xaxis_transform(), color=MUTED, fontsize=7, va="bottom")
    else:
        ax.text(0.5, 0.5, "Collecting: the line fills as the agent polls", transform=ax.transAxes,
                ha="center", color=MUTED, fontsize=9)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=dt.timezone.utc))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=7))
    ax.text(0.01, 0.95, "Share of positions that are long, last 24h", transform=ax.transAxes, color=TEXT,
            fontsize=8.5, va="top")

    # 2. the pulse map
    gx = fig.add_subplot(gs[1])
    gx.set_facecolor(GROUND)
    gx.set_axis_off()
    n = max(len(coins), 1)
    gx.set_xlim(0, 4.2)
    gx.set_ylim(n + 1.05, 0)
    for j, head in enumerate(("1h", "6h", "24h")):
        gx.text(1.2 + j + 0.5, 0.45, head, color=MUTED, fontsize=8, ha="center", va="center")
    gx.text(0.05, 0.45, "Coin · holding now", color=MUTED, fontsize=8, va="center")
    shifts = [abs((c[f"{h}h"] or {}).get("shift", 0)) for c in coins for h in WINDOW_KEYS]
    top = max(shifts + [1])
    for i, c in enumerate(coins):
        y = i + 1
        cur = next((c[f"{h}h"] for h in WINDOW_KEYS if c[f"{h}h"]), None) or {}
        gx.text(0.05, y + 0.42, c["coin"], color=TEXT, fontsize=9, fontweight="bold", va="center")
        gx.text(0.05, y + 0.78, f"{cur.get('long_n', 0)}L · {cur.get('short_n', 0)}S", color=MUTED, fontsize=7,
                va="center")
        for j, h in enumerate(WINDOW_KEYS):
            cell = c[f"{h}h"]
            x0 = 1.2 + j + 0.04
            shift = (cell or {}).get("shift", 0)
            col = UP if shift > 0 else DOWN if shift < 0 else BORDER
            alpha = (0.3 if shift > 0 else 0.25) + 0.45 * min(1, abs(shift) / top) if shift else 0.35
            gx.add_patch(matplotlib.patches.FancyBboxPatch((x0, y + 0.08), 0.92, 0.84,
                         boxstyle="round,pad=0,rounding_size=0.06", facecolor=col, alpha=alpha, linewidth=0))
            if cell:
                gx.text(x0 + 0.46, y + 0.38, f"L{_signed(cell['d_long_n'])}  S{_signed(cell['d_short_n'])}",
                        color=TEXT, fontsize=8.5, ha="center", va="center", family="monospace")
                gx.text(x0 + 0.46, y + 0.7, _usd(cell["net_usd"]), color=TEXT, fontsize=7, ha="center",
                        va="center", alpha=0.85)
            else:
                gx.text(x0 + 0.46, y + 0.5, "–", color=MUTED, fontsize=8, ha="center", va="center")
    if not coins:
        gx.text(2.1, 1.0, "No trader opened or closed yet", color=MUTED, fontsize=9, ha="center")
    fig.text(0.07, 0.3 / height, "L / S: change in traders holding a long / short.  Green: the crowd moved long (longs "
             "opened or shorts closed); red: moved short.\n$: size added long minus short, with the price move taken out.",
             color=MUTED, fontsize=6.5, va="center", linespacing=1.5)
    fig.text(0.97, 0.3 / height, "Outrider", color=MUTED, fontsize=7, ha="right", va="center")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=GROUND)
    return buf.getvalue()


WINDOW_KEYS = (1, 6, 24)


# --------------------------------------------------------------- phone-sized charts --
W_IN, DPI = 5.0, 240       # 1,200 px wide: a chart he reads on a phone without zooming
DAY_MS = 86_400_000


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for sp in ax.spines.values():
        sp.set_color(BORDER)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(color=BORDER, linewidth=0.5, alpha=0.6)


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=GROUND)
    return buf.getvalue()


def _labels(ax, items: list[tuple[float, str, str]], lo: float, hi: float) -> None:
    """Labels at the right edge for horizontal levels, nudged apart so none overlaps another."""
    fig = ax.get_figure()
    height_pt = ax.get_position().height * fig.get_figheight() * 72
    gap = (hi - lo) * 12.5 / max(height_pt, 1)            # one line of 8.5-point text, in data units
    items = sorted(items, key=lambda t: t[0])
    ys: list[float] = []
    for y, _, _ in items:                                  # upwards: each at least a line over the one below
        ys.append(max(y, lo + gap / 2, ys[-1] + gap if ys else y))
    cap = hi - gap / 2
    for i in range(len(ys) - 1, -1, -1):                   # then down from the top, so none leaves the panel
        ys[i] = min(ys[i], cap)
        cap = ys[i] - gap
    for yy, (_, text, col) in zip(ys, items):
        ax.text(1.01, yy, text, transform=ax.get_yaxis_transform(), color=col, fontsize=8.5, va="center",
                ha="left", clip_on=False)


def _band_series(bars: list[list[float]], start: int) -> tuple[list[float | None], list[float | None]]:
    """For each daily bar from `start`: the bull market support band as it stood that day (Monday weeks, the forming
    week's close being that day's close), low and high."""
    from .levels import ema, sma
    wk = [int((b[0] / 1000 - 4 * 86400) // (7 * 86400)) for b in bars]
    week_close: dict[int, float] = {}
    for w, b in zip(wk, bars):
        week_close[w] = b[4]
    order = sorted(week_close)
    lo, hi = [], []
    for i in range(start, len(bars)):
        closes = [week_close[w] for w in order if w < wk[i]] + [bars[i][4]]
        a, e = sma(closes, 20), ema(closes, 21)
        lo.append(min(a, e) if a and e else None)
        hi.append(max(a, e) if a and e else None)
    return lo, hi


def setup_chart(coin: str, side: str, strength: str, bars: list[list[float]], risk: dict | None = None,
                z_hist: list[tuple[float, float]] | None = None, entries: list[tuple[float, float, str]] | None = None,
                show: int = 120, patterns: list[dict] | None = None) -> bytes:
    """A setup on its daily chart: the last `show` days of candles, the bull market support band as it stood each day,
    the 200-day average, where the setup is wrong and the next level the other way, tracked traders' entries, and the
    engine's daily z-score with the owner's lines under it when its history is long enough to draw."""
    import numpy as np
    closes = [b[4] for b in bars]
    start = max(0, len(bars) - show)
    view = bars[start:]
    x = np.arange(len(view))
    ma200 = [sum(closes[i - 199:i + 1]) / 200 if i >= 199 else None for i in range(start, len(bars))]
    blo, bhi = _band_series(bars, start)
    zs = [(t, z) for t, z in (z_hist or []) if z is not None and t >= view[0][0] / 1000]
    with_z = len(zs) >= 10
    fig = Figure(figsize=(W_IN, 5.3), dpi=DPI)
    fig.patch.set_facecolor(GROUND)
    if with_z:
        gs = fig.add_gridspec(2, 1, height_ratios=[3, 1.25], hspace=0.08, left=0.03, right=0.78, top=0.86,
                              bottom=0.08)
        ax, zx = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])
        _style(zx)
        zx.yaxis.tick_right()
    else:
        ax = fig.add_axes((0.03, 0.08, 0.75, 0.78))
        zx = None
    _style(ax)
    ax.yaxis.tick_right()
    o, h, low, c = (np.array([b[i] for b in view]) for i in (1, 2, 3, 4))
    up = c >= o
    cols = np.where(up, UP, DOWN)
    ax.vlines(x, low, h, color=cols, linewidth=0.7, alpha=0.85)
    span = float(h.max() - low.min()) or 1.0
    ax.bar(x, np.maximum(abs(c - o), span * 0.003), bottom=np.minimum(o, c), color=cols, width=0.7, linewidth=0)
    bx = [i for i, v in enumerate(blo) if v is not None]
    if bx:
        ax.fill_between(bx, [blo[i] for i in bx], [bhi[i] for i in bx], color=ACCENT, alpha=0.22, linewidth=0)
    mx = [i for i, v in enumerate(ma200) if v is not None]
    if mx:
        ax.plot(mx, [ma200[i] for i in mx], color=TEXT, linewidth=1.1, linestyle=(0, (4, 2)))
    for t, px, s in entries or []:
        i = int(round((t * 1000 - view[0][0]) / DAY_MS))
        if 0 <= i < len(view):
            ax.scatter([i], [px], s=34, color=DOWN if s == "short" else UP, edgecolor=GROUND, linewidth=0.7,
                       zorder=4, marker="v" if s == "short" else "^")
    pat_note = ""
    pat_items: list[tuple[float, str, str]] = []
    for p in (patterns or [])[:2]:                # Reflex's chart patterns: their shape, break level and fail level
        pts = [((t * 1000 - view[0][0]) / DAY_MS, v) for t, v in p.get("anchors") or [] if t and v]
        pts = [(i, v) for i, v in pts if -5 <= i <= len(view) + 5]
        if len(pts) >= 2:
            ax.plot([i for i, _ in pts], [v for _, v in pts], color=ACCENT, linewidth=1.1, alpha=0.75,
                    marker="o", markersize=2.5)
        pcol = UP if p.get("direction") == "up" else DOWN if p.get("direction") == "down" else MUTED
        for v in p.get("break_level") or []:
            ax.axhline(v, color=pcol, linewidth=0.9, alpha=0.8)
            pat_items.append((v, f"break {_fmt(v)}", pcol))
        if not pat_note and p.get("pattern"):
            pat_note = f"{p['pattern']} ({p.get('status')})" + (f": break {_fmt(p['break_level'][0])}"
                                                               if p.get("break_level") else "")
    levels_y = [float(v) for v in (low.min(), h.max())] + [v for v, _, _ in pat_items]
    recent = len(view) - 60                                     # the band and the 200-day near today stay in view
    levels_y += [v for i, v in enumerate(blo) if v is not None and i >= recent]
    levels_y += [v for i, v in enumerate(bhi) if v is not None and i >= recent]
    levels_y += [v for i, v in enumerate(ma200) if v is not None and i >= recent]
    items: list[tuple[float, str, str]] = [(float(c[-1]), f"now {_fmt(float(c[-1]))}", TEXT)] + pat_items[:2]
    off, nxt = (risk or {}).get("off"), (risk or {}).get("next")
    if off:
        ax.axhline(off["level"], color=ACCENT, linewidth=1.2, linestyle="--")
        items.append((off["level"], f"off {_fmt(off['level'])}", ACCENT))
        levels_y.append(off["level"])
    if nxt:
        ax.axhline(nxt["level"], color=MUTED, linewidth=1.0, linestyle=":")
        items.append((nxt["level"], f"next {_fmt(nxt['level'])}", MUTED))
        levels_y.append(nxt["level"])
    ylo, yhi = min(levels_y), max(levels_y)
    pad = (yhi - ylo) * 0.06
    ax.set_ylim(ylo - pad, yhi + pad)
    ax.set_xlim(-1, len(view) + 1)
    ax.tick_params(labelright=False)
    _labels(ax, items, ylo - pad, yhi + pad)
    ticks = [i for i in range(0, len(view), 30)]
    fmt = lambda i: dt.datetime.fromtimestamp(view[i][0] / 1000, dt.timezone.utc).strftime("%d %b")  # noqa: E731
    target = zx if zx is not None else ax
    if zx is not None:
        ax.set_xticks(ticks)
        ax.tick_params(labelbottom=False)
        zxs = [(t * 1000 - view[0][0]) / DAY_MS for t, _ in zs]
        zx.plot(zxs, [z for _, z in zs], color=TEXT, linewidth=1.3)
        zx.scatter([zxs[-1]], [zs[-1][1]], s=24, color=ACCENT, zorder=4)
        for v, col in ((3.5, DOWN), (3.0, DOWN), (2.5, ACCENT), (-1.0, UP)):
            zx.axhline(v, color=col, linewidth=0.8, linestyle="--", alpha=0.9)
        zlo = min(-1.5, min(z for _, z in zs) - 0.3)
        zhi = max(3.8, max(z for _, z in zs) + 0.3)
        zx.set_ylim(zlo, zhi)
        zx.set_xlim(-1, len(view) + 1)
        zx.tick_params(labelright=False)
        _labels(zx, [(3.5, "3.5", DOWN), (3.0, "3", DOWN), (2.5, "2.5", ACCENT), (-1.0, "-1", UP)], zlo, zhi)
        zx.text(0.01, 0.06, f"daily z {zs[-1][1]:.1f}", transform=zx.transAxes, color=TEXT, fontsize=9, va="bottom",
                bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1})
    target.set_xticks(ticks)
    target.set_xticklabels([fmt(i) for i in ticks])
    col = DOWN if side == "short" else UP
    fig.text(0.03, 0.955, f"{coin} {side} · {strength}", color=col, fontsize=14, fontweight="bold", va="center")
    sub = "Band shaded · 200-day dashed · off level orange · next dotted"
    if pat_note:
        ax.text(0.01, 0.02, pat_note, transform=ax.transAxes, color=TEXT, fontsize=8.5,
                bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1})
    if not with_z and zs:
        sub += f" · daily z {zs[-1][1]:.1f}"
    fig.text(0.03, 0.905, sub, color=MUTED, fontsize=8.5, va="center")
    fig.text(0.97, 0.02, "UTC · Outrider", color=MUTED, fontsize=8.5, ha="right")
    return _png(fig)


def market_map(rows: list[dict], cands: list[dict], label: list[str] = (), title: str | None = None) -> bytes:
    """Every screened coin by where it sits against its band (x, % from the band's middle) and its daily z-score (y),
    with the owner's lines; long candidates green, short red, the rest grey; size by open interest."""
    import math
    pts = []
    for r in rows:
        b, z = r.get("band"), r.get("z_1d")
        if not b or z is None:
            continue
        mid = (b["low"] + b["high"]) / 2
        pts.append((r, max(-40.0, min(60.0, 100 * (r["price"] / mid - 1))), max(-2.5, min(4.5, z))))
    side = {c["coin"]: c["side"] for c in cands}
    top_oi = max([r.get("open_interest_usd") or 0 for r, _, _ in pts] + [1])
    fig = Figure(figsize=(W_IN, 5.3), dpi=DPI)
    fig.patch.set_facecolor(GROUND)
    ax = fig.add_axes((0.12, 0.1, 0.84, 0.76))
    _style(ax)
    ax.axvspan(-4, 4, color=ACCENT, alpha=0.10, linewidth=0)
    ax.axvline(0, color=ACCENT, linewidth=0.8, alpha=0.6)
    for v, txt, col in ((3.0, "3 trim", DOWN), (2.5, "2.5 blow-off", ACCENT), (-1.0, "-1 capitulation", UP)):
        ax.axhline(v, color=col, linewidth=0.9, linestyle="--")
        ax.text(0.99, v, txt, transform=ax.get_yaxis_transform(), color=col, fontsize=8.5, ha="right", va="bottom")
    want = set(label) | {c["coin"] for c in cands[:6]} | {"BTC", "ETH"}
    for r, x, y in sorted(pts, key=lambda p: p[0]["coin"] in side):
        s = side.get(r["coin"])
        size = 10 + 240 * math.sqrt((r.get("open_interest_usd") or 0) / top_oi)
        ax.scatter([x], [y], s=size, color=UP if s == "long" else DOWN if s == "short" else MUTED,
                   alpha=0.85 if s else 0.35, edgecolor=GROUND, linewidth=0.5, zorder=3 if s else 2)
        if r["coin"] in want:
            ax.annotate(r["coin"], (x, y), xytext=(5, 4), textcoords="offset points", color=TEXT, fontsize=8.5,
                        zorder=5)
    ax.set_xlim(-42, 62)
    ax.set_ylim(-2.7, 4.7)
    ax.set_xlabel("Price against its bull market support band (%)", color=MUTED, fontsize=9)
    ax.set_ylabel("Daily z-score", color=MUTED, fontsize=9)
    fig.text(0.03, 0.955, title or f"Market map · {len(pts)} coins", color=TEXT, fontsize=14, fontweight="bold",
             va="center")
    fig.text(0.03, 0.91, "Green: long candidate · red: short · grey: none · size: open interest", color=MUTED,
             fontsize=8.5, va="center")
    fig.text(0.97, 0.02, "Outrider", color=MUTED, fontsize=8.5, ha="right")
    return _png(fig)


def journal_chart(picks: list[dict], bar: dict, since: float, label: str = "Setups since 3 Nov (S5)") -> bytes | None:
    """The record against its declared bar (S5 Amendment 3), not a sum of returns: over separate bets, the running
    share that beat the market (the verdict's line) with the raw share right beside it, the bar (62%) and the number
    needed (60); under it each bet's 3-day move beyond the market and the running median against the +1% bar. None
    until one bet is scored."""
    import statistics
    from .journal import excess, first_bets, s5
    got = [p for p in first_bets([p for p in picks if p["ts"] >= since]) if p.get("ret_3d") is not None]
    if not got:
        return None
    n = list(range(1, len(got) + 1))
    right = [100 * sum(p["ret_3d"] > 0 for p in got[:i]) / i for i in n]
    xs = [(i, x) for i, p in enumerate(got, 1) if (x := excess(p, "3d")) is not None]
    beat = [(i, 100 * sum(x > 0 for _, x in xs[:k]) / k) for k, (i, _) in enumerate(xs, 1)]
    moves = [(i, max(-15.0, min(15.0, 100 * x))) for i, x in xs]
    med = [(i, statistics.median(m for _, m in moves[:k])) for k, (i, _) in enumerate(moves, 1)]
    fig = Figure(figsize=(W_IN, 5.0), dpi=DPI)
    fig.patch.set_facecolor(GROUND)
    gs = fig.add_gridspec(2, 1, height_ratios=[1.4, 1], hspace=0.12, left=0.12, right=0.95, top=0.84, bottom=0.1)
    ax, mx = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])
    _style(ax)
    _style(mx)
    if beat:
        ax.plot([i for i, _ in beat], [v for _, v in beat], color=ACCENT, linewidth=1.8, label="beat the market")
    ax.plot(n, right, color=MUTED, linewidth=1.2, linestyle="--", label="right (raw)")
    ax.axhline(bar["right_pct"], color=UP, linewidth=1, linestyle="--")
    ax.axhline(50, color=MUTED, linewidth=0.8, linestyle=":")
    xmax = max(bar["n"], len(got)) + 2
    ax.axvline(bar["n"], color=MUTED, linewidth=0.8, linestyle="--")
    ax.text(bar["n"], 3, f"{bar['n']} needed ", color=MUTED, fontsize=8.5, ha="right")
    ax.text(xmax, bar["right_pct"], f"bar {bar['right_pct']}%", color=UP, fontsize=8.5, ha="right", va="bottom")
    ax.set_ylim(0, 100)
    ax.set_xlim(0, xmax)
    ax.tick_params(labelbottom=False)
    ax.legend(loc="lower left", fontsize=8.5, frameon=False, labelcolor=TEXT)
    if moves:
        cols = [UP if got[i - 1]["direction"] == "long" else DOWN for i, _ in moves]
        mx.scatter([i for i, _ in moves], [m for _, m in moves], s=18, color=cols, zorder=3)
        mx.plot([i for i, _ in med], [m for _, m in med], color=TEXT, linewidth=1.3)
    mx.axhline(bar["median_pct"], color=UP, linewidth=1, linestyle="--")
    mx.axhline(0, color=MUTED, linewidth=0.8, linestyle=":")
    mx.set_xlim(0, xmax)
    mx.set_ylabel("beyond the market %", color=MUTED, fontsize=9)
    mx.set_xlabel("separate bet", color=MUTED, fontsize=9)
    met = s5(got, bar=bar)["verdict"] == "meets the declared bar"
    fig.text(0.03, 0.955, f"{label}: {len(got)} bets scored", color=TEXT, fontsize=13, fontweight="bold",
             va="center")
    fig.text(0.03, 0.905, "Meets the declared bar" if met else "Context, not a signal: under the declared bar",
             color=UP if met else MUTED, fontsize=9, va="center")
    fig.text(0.97, 0.02, "Outrider", color=MUTED, fontsize=8.5, ha="right")
    return _png(fig)


def cycle_chart(hist: dict, turns: dict | None = None) -> bytes | None:
    """The market cycle from Reflex's history: the share of coins in an uptrend each day with the broad-uptrend line,
    past runs shaded faint and the current one in orange, BTC beside it; under it the median z-score."""
    import datetime as _dt
    rows = [r for r in hist.get("rows") or [] if r and r[0] and r[1] is not None]
    if len(rows) < 30:
        return None
    day = lambda d: _dt.datetime.strptime(str(d)[:10], "%Y-%m-%d")   # noqa: E731
    t = [day(r[0]) for r in rows]
    share = [r[1] for r in rows]
    z = [(day(r[0]), r[3]) for r in rows if len(r) > 3 and r[3] is not None]
    cyc = hist.get("cycle") or {}
    fig = Figure(figsize=(W_IN, 4.6), dpi=DPI)
    fig.patch.set_facecolor(GROUND)
    if len(z) >= 30:
        gs = fig.add_gridspec(2, 1, height_ratios=[2.4, 1], hspace=0.1, left=0.1, right=0.88, top=0.84, bottom=0.1)
        ax = fig.add_subplot(gs[0])
        zx = fig.add_subplot(gs[1], sharex=ax)
        _style(zx)
    else:
        ax, zx = fig.add_axes((0.1, 0.1, 0.78, 0.74)), None
    _style(ax)
    shade = DOWN if cyc.get("type") == "off" else UP       # past runs of the current kind: uptrends or downturns
    for r in cyc.get("runs") or []:
        try:
            a = day(r[0])
            b = a + _dt.timedelta(days=int(r[1] or 0))
        except (TypeError, ValueError):
            continue
        if b < t[0]:
            continue                                        # runs before the window would stretch it
        ax.axvspan(max(a, t[0]), min(b, t[-1]), color=shade, alpha=0.08, linewidth=0)
    if cyc.get("start"):
        ax.axvspan(max(day(cyc["start"]), t[0]), t[-1], color=ACCENT, alpha=0.14, linewidth=0)
    ax.plot(t, share, color=TEXT, linewidth=1.4)
    ax.set_xlim(t[0], t[-1])
    if cyc.get("threshold") is not None:
        thr = float(cyc["threshold"])                      # Reflex sends a fraction (0.55); the axis is in percent
        ax.axhline(100 * thr if thr <= 1 else thr, color=MUTED, linewidth=0.9, linestyle="--")
    ax.set_ylim(0, 100)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    btc = [(day(r[0]), r[2]) for r in rows if len(r) > 2 and r[2]]
    if btc:
        bx = ax.twinx()
        bx.plot([a for a, _ in btc], [b for _, b in btc], color=MUTED, linewidth=0.9, alpha=0.8)
        bx.set_yscale("log")
        bx.tick_params(colors=MUTED, labelsize=8.5)
        bx.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1000:.0f}K"))
        for sp in bx.spines.values():
            sp.set_color(BORDER)
    if zx is not None:
        zx.plot([a for a, _ in z], [b for _, b in z], color=ACCENT, linewidth=1.2)
        zx.axhline(0, color=MUTED, linewidth=0.8, linestyle=":")
        zx.set_ylabel("median z", color=MUTED, fontsize=9)
        ax.tick_params(labelbottom=False)
        target = zx
    else:
        target = ax
    target.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    target.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    state = (turns or {}).get("state")
    head = "Market cycle" + (f" · {state}" if state else "")
    if state == "broad uptrend" and turns.get("run_day") is not None:
        head += f", day {turns['run_day']}"
    elif state == "broad downturn" and turns.get("downturn_day") is not None:
        head += f", day {turns['downturn_day']}"
    fig.text(0.03, 0.955, head, color=TEXT, fontsize=13, fontweight="bold", va="center")
    fig.text(0.03, 0.905, "Share of coins in an uptrend · dashed: the broad-uptrend line · grey: BTC", color=MUTED,
             fontsize=8.5, va="center")
    fig.text(0.97, 0.02, "Outrider", color=MUTED, fontsize=8.5, ha="right")
    return _png(fig)


def breadth_chart(days: list[dict]) -> bytes | None:
    """The market's breadth day by day: the share of coins over their band and over their 200-day average, and the
    share inside the band shaded."""
    import datetime as _dt
    days = [d for d in days if d.get("coins")]
    if len(days) < 10:
        return None
    t = [_dt.datetime.strptime(d["day"], "%Y-%m-%d") for d in days]
    over = [100 * d["over"] / d["coins"] for d in days]
    inside = [100 * d["inside"] / d["coins"] for d in days]
    o200 = [100 * d["over_200d"] / d["with_200d"] if d["with_200d"] else None for d in days]
    fig = Figure(figsize=(W_IN, 3.8), dpi=DPI)
    fig.patch.set_facecolor(GROUND)
    ax = fig.add_axes((0.1, 0.12, 0.86, 0.68))
    _style(ax)
    ax.fill_between(t, over, [a + b for a, b in zip(over, inside)], color=ACCENT, alpha=0.18, linewidth=0,
                    label="inside the band")
    ax.plot(t, over, color=UP, linewidth=1.6, label="over the band")
    ax.plot([a for a, b in zip(t, o200) if b is not None], [b for b in o200 if b is not None], color=TEXT,
            linewidth=1.1, linestyle="--", label="over the 200-day")
    ax.axhline(50, color=MUTED, linewidth=0.8, linestyle=":")
    ax.set_ylim(0, 100)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.legend(loc="upper left", fontsize=8.5, frameon=False, labelcolor=TEXT, ncol=3)
    fig.text(0.03, 0.94, f"Breadth · {days[-1]['coins']} coins", color=TEXT, fontsize=13, fontweight="bold",
             va="center")
    fig.text(0.03, 0.875, "Today's most traded coins, rebuilt from their own candles (survivorship: coins that fell "
             "out are missing)", color=MUTED, fontsize=8.5, va="center")
    fig.text(0.97, 0.02, "Outrider", color=MUTED, fontsize=8.5, ha="right")
    return _png(fig)
