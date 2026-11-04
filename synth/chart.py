"""Charts for Telegram and the dashboard, drawn in code at no cost: a group's 48 hours, the live pulse, a setup on its
daily chart, the market map and the record against its bar.

Pure functions of their inputs; each returns PNG bytes. Figures are built with matplotlib's object API (no pyplot
state), so two renders in two threads never mix. Sized for a phone: 5 inches wide at 240 dpi (1,200 px, the long side
under Telegram's 1,280), no text under 8.5 points.
"""

from __future__ import annotations

import datetime as dt
import io
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.lines  # noqa: E402,F401
import matplotlib.patches  # noqa: E402,F401
import matplotlib.ticker  # noqa: E402,F401
from matplotlib.figure import Figure  # noqa: E402

GROUND, SURFACE, BORDER = "#0B1030", "#121A3C", "#33417A"     # deep indigo, a lighter panel, its edge
TEXT, MUTED = "#F4F6FF", "#A3AFD6"
UP, DOWN, ACCENT = "#2EF2A6", "#FF4D7D", "#FFB627"           # vivid mint, coral, amber
matplotlib.rcParams["font.family"] = ["Inter", "DejaVu Sans"]   # Inter where installed (the image has it)
logging_ = __import__("logging")
logging_.getLogger("matplotlib.font_manager").setLevel(logging_.ERROR)


def _fmt(x: float) -> str:
    return f"{x:,.0f}" if x >= 1000 else f"{x:.2f}" if x >= 1 else f"{x:.4g}"


def group_chart(coin: str, side: str, candles: list[list[float]], members: list[dict],
                all_losing: float | None, title: str) -> bytes:
    """candles: [[t_ms, o, h, l, c]]; members: [{entry_px, opened_ts, status}]."""
    fig = Figure(figsize=(8, 4.5), dpi=225)
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
    fig = Figure(figsize=(8, height), dpi=225)
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
W_IN, DPI = 5.0, 360       # 1,800 px wide: sharp on a phone, under Telegram's 2,560 for a photo
DAY_MS = 86_400_000
BG_TOP, BG_BOTTOM = "#0B1030", "#17204A"          # deep indigo, lighter toward the foot: depth behind the panels
PANEL, GRID, EDGE = "#121A3C", "#26325E", "#33417A"
BAND, BAND_EDGE = "#8B5CF6", "#B9A2FF"            # the bull market support band: violet
MA200 = "#38BDF8"                                 # the 200-day average: sky blue
OFF = "#FFB627"                                   # where a setup is wrong: amber
NEXT = "#67E8F9"                                  # the next level the other way: cyan
DIM = "#5D6B9E"                                   # coins with nothing to say on the map
FONT = ["Inter", "DejaVu Sans"]


def _canvas(fig) -> None:
    """A deep background with a little light from the top: a vertical gradient and a soft glow behind the title."""
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap
    bg = fig.add_axes((0, 0, 1, 1), zorder=-10)
    bg.axis("off")
    grad = np.linspace(0, 1, 512).reshape(-1, 1)
    bg.imshow(grad, aspect="auto", extent=(0, 1, 0, 1), origin="upper",
              cmap=LinearSegmentedColormap.from_list("bg", [BG_TOP, BG_BOTTOM]))
    yy, xx = np.mgrid[0:1:200j, 0:1:200j]
    glow = np.exp(-(((xx - 0.25) / 0.45) ** 2 + ((yy - 0.0) / 0.35) ** 2))      # top left
    rgba = np.zeros((200, 200, 4))
    rgba[..., :3] = matplotlib.colors.to_rgb("#3B4BA8")
    rgba[..., 3] = 0.35 * glow
    bg.imshow(rgba, aspect="auto", extent=(0, 1, 0, 1), origin="upper")
    bg.set_xlim(0, 1)
    bg.set_ylim(0, 1)


def _style(ax) -> None:
    ax.set_facecolor((*matplotlib.colors.to_rgb(PANEL), 0.72))
    for sp in ax.spines.values():
        sp.set_color(EDGE)
        sp.set_linewidth(0.8)
    ax.tick_params(colors=MUTED, labelsize=9, length=0, pad=4)
    ax.grid(color=GRID, linewidth=0.6, alpha=0.9)
    ax.set_axisbelow(True)


def _glow(color: str, width: float = 5.0, alpha: float = 0.28) -> list:
    """A soft halo under a line or a point, so the levels that matter stand out from the panel."""
    from matplotlib import patheffects as pe
    return [pe.Stroke(linewidth=width, foreground=color, alpha=alpha), pe.Normal()]


def _outline(width: float = 3.0) -> list:
    """Text readable over anything: a thin dark edge around the letters."""
    from matplotlib import patheffects as pe
    return [pe.withStroke(linewidth=width, foreground=BG_TOP)]


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG_TOP)
    return buf.getvalue()


def _header(fig, title: str, sub: str = "", color: str | None = None, y: float = 0.955) -> None:
    fig.text(0.04, y, title, color=color or TEXT, fontsize=15, fontweight="bold", va="center")
    if sub:
        fig.text(0.04, y - 0.046, sub, color=MUTED, fontsize=9, va="center")
    fig.text(0.96, 0.018, "Outrider", color=MUTED, fontsize=8.5, ha="right", va="bottom", alpha=0.85)


def _labels(ax, items: list[tuple[float, str, str]], lo: float, hi: float) -> None:
    """Labels at the right edge for horizontal levels, on a dark tag of their colour, nudged apart so none overlaps;
    a tag nudged off its level keeps a short leader to it."""
    fig = ax.get_figure()
    height_pt = ax.get_position().height * fig.get_figheight() * 72
    gap = (hi - lo) * 19.0 / max(height_pt, 1)            # one tag of 9-point text with its frame, in data units
    items = sorted(items, key=lambda t: t[0])
    ys: list[float] = []
    for y, _, _ in items:                                  # upwards: each at least a line over the one below
        ys.append(max(y, lo + gap / 2, ys[-1] + gap if ys else y))
    cap = hi - gap / 2
    for i in range(len(ys) - 1, -1, -1):                   # then down from the top, so none leaves the panel
        ys[i] = min(ys[i], cap)
        cap = ys[i] - gap
    tr = ax.get_yaxis_transform()
    for yy, (y0, text, col) in zip(ys, items):
        at = min(max(y0, lo), hi)
        if abs(yy - at) > gap * 0.25:
            ax.add_line(matplotlib.lines.Line2D([1.0, 1.008, 1.015], [at, yy, yy], transform=tr, color=col,
                                                linewidth=0.8, clip_on=False, alpha=0.9))
        ax.text(1.015, yy, text, transform=tr, color=col, fontsize=9, fontweight="semibold",
                va="center", ha="left", clip_on=False,
                bbox={"boxstyle": "round,pad=0.28,rounding_size=0.35", "facecolor": BG_TOP, "edgecolor": col,
                      "linewidth": 0.8, "alpha": 0.95})


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


def _legend(fig, items: list[tuple[str, str, str]], y: float) -> None:
    """A row of small keys under the title: a swatch of each line's colour and style, then its name ("tri": a mint
    up and a coral down triangle, for the traders)."""
    h = 0.075 / fig.get_figheight()
    x = 0.04
    for kind, col, name in items:
        if kind == "fill":
            fig.patches.append(matplotlib.patches.FancyBboxPatch(
                (x, y - h / 2), 0.03, h, boxstyle="round,pad=0,rounding_size=0.004", transform=fig.transFigure,
                facecolor=col, alpha=0.55, edgecolor=BAND_EDGE, linewidth=0.6))
        elif kind == "tri":
            for dx, mk, c in ((0.007, "^", UP), (0.024, "v", DOWN)):
                fig.add_artist(matplotlib.lines.Line2D([x + dx], [y], transform=fig.transFigure, marker=mk,
                                                       markersize=6.5, color=c, linestyle="none"))
        else:
            ls = {"dash": (0, (3, 1.6)), "dot": (0, (1, 1.6)), "line": "-"}[kind]
            fig.add_artist(matplotlib.lines.Line2D([x, x + 0.03], [y, y], transform=fig.transFigure, color=col,
                                                   linewidth=1.6, linestyle=ls))
        fig.text(x + 0.037, y, name, color=MUTED, fontsize=8.5, va="center")
        x += 0.065 + 0.0145 * len(name)


# ------------------------------------------------------------------------- the crowd on a setup chart --
INK = "#0B1030"              # dark text on bright fills
S_MIN, S_MAX = 70.0, 800.0   # triangle area in points²: the smallest position / a whole side's money in one place
MAX_CLUSTERS = 4             # triangles per side on the dated part of the panel (the "earlier" column holds one)
MERGE_PT = 14.0              # two triangles merge when their edges come within 14 points of each other
GUTTER = 15                  # days of width given to the "earlier" column, left of the first candle
WEEK = 7


def crowd_positions(entries: list | None) -> list[dict]:
    """Tracked traders' positions as {since, entry, side, value}. Older callers pass (when, entry, side) tuples; when
    no position carries a size, each counts once (value 1) and the chart says "by count"."""
    out = []
    for e in entries or []:
        if isinstance(e, dict):
            p = {"since": e.get("since"), "entry": e.get("entry"), "side": e.get("side"),
                 "value": float(e.get("value") or 0)}
        else:
            p = {"since": e[0], "entry": e[1], "side": e[2], "value": 0.0}
        if p["entry"] and p["entry"] > 0 and p["side"] in ("long", "short"):
            out.append(p)
    if out and not any(p["value"] > 0 for p in out):
        for p in out:
            p["value"] = 1.0
            p["unsized"] = True
    return [p for p in out if p["value"] > 0]


def _wmedian(vals: list[float], weights: list[float]) -> float:
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    half, acc = sum(weights) / 2, 0.0
    for i in order:
        acc += weights[i]
        if acc >= half:
            return vals[i]
    return vals[order[-1]]


def crowd_summary(positions: list[dict], now: float) -> dict:
    """Per side: traders, money (each position's dollar size now), the average entry weighted by money (so by the
    coins held), the price now against it (percent, before leverage and funding: positive is in profit), and the
    money-weighted median open time over the positions with a known one. Then the share long by money."""
    out: dict = {"sized": not any(p.get("unsized") for p in positions)}
    for side in ("long", "short"):
        ps = [p for p in positions if p["side"] == side]
        usd = sum(p["value"] for p in ps)
        avg = sum(p["entry"] * p["value"] for p in ps) / usd if usd else None
        pnl = 100 * (now / avg - 1) * (1 if side == "long" else -1) if avg else None
        known = [p for p in ps if p.get("since")]
        med = _wmedian([p["since"] for p in known], [p["value"] for p in known]) if known else None
        out[side] = {"n": len(ps), "usd": usd, "avg": avg, "pnl": pnl, "since_median": med, "ps": ps}
    tot = out["long"]["usd"] + out["short"]["usd"]
    out["long_share"] = 100 * out["long"]["usd"] / tot if tot else None
    out["n"] = out["long"]["n"] + out["short"]["n"]
    return out


def crowd_clusters(points: list[dict], size, to_pt, cap: int, merge_pt: float = MERGE_PT) -> list[dict]:
    """Merge triangles that crowd each other: join the closest pair (the gap between their edges, in points on the
    page) while any pair comes within `merge_pt`, or while there are more than `cap`. A merged triangle sits at its
    members' money-weighted mean day and mean entry (that is their average entry) and holds their money and count.
    points: [{x, y, v, n}]; size(v) is a triangle's area in points²; to_pt(c) its centre in points."""
    cl = [dict(p) for p in points]
    while len(cl) > 1:
        pts = [to_pt(c) for c in cl]
        half = [math.sqrt(size(c["v"])) / 2 for c in cl]
        best = None
        for i in range(len(cl)):
            for j in range(i + 1, len(cl)):
                g = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]) - (half[i] + half[j])
                if best is None or g < best[0]:
                    best = (g, i, j)
        if best[0] >= merge_pt and len(cl) <= cap:
            break
        _, i, j = best
        a, b = cl[i], cl[j]
        v = a["v"] + b["v"]
        cl = [c for k, c in enumerate(cl) if k not in (i, j)] + [
            {"x": (a["x"] * a["v"] + b["x"] * b["v"]) / v, "y": (a["y"] * a["v"] + b["y"] * b["v"]) / v,
             "v": v, "n": a["n"] + b["n"]}]
    return cl


def _day(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%-d %b")


def _money(v: float, sized: bool) -> str:
    return _usd(v).lstrip("+") if sized else ""


def _crowd_block(fig, s: dict, y_top: float) -> None:
    """Under the title: who holds it (share long by money), one bar split mint and coral by money with each side's
    traders and dollars over its end, and under it each side's average entry against the price now."""
    H = fig.get_figheight()
    f = lambda inch: inch / H  # noqa: E731
    L, R = 0.04, 0.96
    share = s["long_share"]
    lg, sh = s["long"], s["short"]
    by = "by money" if s["sized"] else "by count"
    if share >= 55:
        verdict, vcol = f"Traders {share:.0f}% long {by}", UP
    elif share <= 45:
        verdict, vcol = f"Traders {100 - share:.0f}% short {by}", DOWN
    else:
        verdict, vcol = f"Traders split: {share:.0f}% long {by}", TEXT
    fig.text(L, y_top, verdict, color=vcol, fontsize=12.5, fontweight="bold", va="center")
    y_lab = y_top - f(0.29)
    for d, word, x, ha, col in ((lg, "long", L, "left", UP), (sh, "short", R, "right", DOWN)):
        if d["n"]:
            money = _money(d["usd"], s["sized"])
            fig.text(x, y_lab, f"{d['n']} {word}" + (f" · {money}" if money else ""), color=col, fontsize=9.5,
                     fontweight="bold", va="center", ha=ha)
        else:
            fig.text(x, y_lab, f"no {word}s", color=MUTED, fontsize=9.5, va="center", ha=ha)
    bh = f(0.15)
    y_bar = y_lab - f(0.13) - bh
    w = R - L
    split = L + w * share / 100
    frame = matplotlib.patches.FancyBboxPatch((L, y_bar), w, bh, boxstyle="round,pad=0,rounding_size=0.006",
                                              transform=fig.transFigure, facecolor=DOWN, edgecolor="none")
    fig.patches.append(frame)
    if share > 0:
        rect = matplotlib.patches.Rectangle((L, y_bar), split - L, bh, transform=fig.transFigure, facecolor=UP,
                                            edgecolor="none")
        rect.set_clip_path(frame)
        fig.patches.append(rect)
    fig.add_artist(matplotlib.lines.Line2D([L + w / 2] * 2, [y_bar - f(0.035), y_bar + bh + f(0.035)],
                                           transform=fig.transFigure, color=MUTED, linewidth=0.8, alpha=0.8))
    if 0 < share < 100:
        fig.add_artist(matplotlib.lines.Line2D([split] * 2, [y_bar - f(0.03), y_bar + bh + f(0.03)],
                                               transform=fig.transFigure, color=TEXT, linewidth=1.8))
    y_avg = y_bar - f(0.16)
    texts = []
    for d, x, ha, col in ((lg, L, "left", UP), (sh, R, "right", DOWN)):
        if d["n"]:
            word = "in profit" if d["pnl"] >= 0 else "under water"
            texts.append(fig.text(x, y_avg, f"avg entry {_fmt(d['avg'])} · {d['pnl']:+.1f}% {word}", color=col,
                                  fontsize=9, va="center", ha=ha, fontweight="semibold"))
    if len(texts) == 2:                                  # long prices on both sides: smaller until they clear
        for size in (8.5, 8.0):
            a, b = (t.get_window_extent() for t in texts)
            if a.x1 + 12 < b.x0:
                break
            for t in texts:
                t.set_fontsize(size)


def _crowd_weeks(cx, s: dict, n: int, v0: float, gx: float, early: bool) -> None:
    """The slim panel under the price: the money in positions opened each week (the last week ending today), longs
    as mint bars up and shorts as coral bars down; positions opened earlier, or before tracking began, in the
    "earlier" column."""
    weeks: dict[int, list[float]] = {}
    before = [0.0, 0.0]
    for k, side in enumerate(("long", "short")):
        for p in s[side]["ps"]:
            if p.get("since") and p["since"] >= v0:
                i = min((p["since"] - v0) / 86400, n - 1)
                weeks.setdefault(int((n - 1 - i) // WEEK), [0.0, 0.0])[k] += p["value"]
            else:
                before[k] += p["value"]
    top = max([max(v) for v in weeks.values()] + [0.0]) or max(before) or 1.0
    lim = top * 1.3
    for w, (lv, sv) in weeks.items():
        xc = n - 1 - w * WEEK - (WEEK - 1) / 2
        if lv:
            cx.bar(xc, lv, width=WEEK - 1.2, color=UP, alpha=0.9, linewidth=0, zorder=3)
        if sv:
            cx.bar(xc, -sv, width=WEEK - 1.2, color=DOWN, alpha=0.9, linewidth=0, zorder=3)
    if early:
        for v, sign, col in ((before[0], 1, UP), (before[1], -1, DOWN)):
            if not v:
                continue
            shown = min(v, top * 1.15)
            cx.bar(gx, sign * shown, width=GUTTER * 0.5, color=col, alpha=0.5, linewidth=0, zorder=3)
            if v > shown and s["sized"]:                 # taller than the panel: its amount written on it
                cx.text(gx, sign * top * 0.55, _money(v, True), color=TEXT, fontsize=8.5, fontweight="bold",
                        ha="center", va="center", zorder=4, path_effects=_outline(2.4))
    cx.axhline(0, color=MUTED, linewidth=0.8, alpha=0.7, zorder=2)
    cx.set_ylim(-lim, lim)
    cx.set_yticks([-top, top])                           # the tallest week, up and down
    cx.set_yticklabels([_money(top, s["sized"]) or str(int(top))] * 2)
    cx.text(1.015, 0.5, "$ by week\nopened" if s["sized"] else "traders by\nweek opened", transform=cx.transAxes,
            color=MUTED, fontsize=8.5, va="center", ha="left", linespacing=1.3)


def setup_chart(coin: str, side: str, strength: str, bars: list[list[float]], risk: dict | None = None,
                z_hist: list[tuple[float, float]] | None = None, entries: list | None = None,
                show: int = 120, patterns: list[dict] | None = None) -> bytes:
    """A setup on its daily chart: the last `show` days of candles, the bull market support band as it stood each day,
    the 200-day average, where the setup is wrong and the next level the other way, a chart pattern Reflex found, and
    the engine's daily z-score with the owner's lines under it once its history is long enough.

    With tracked traders' positions (`entries`, see crowd_positions), the crowd reads at a glance: under the title the
    share long by money, a mint and coral bar with each side's traders and dollars, and each side's average entry
    against the price now; on the chart each side's average entry as a line from the day by which half its money was
    in, and its positions as triangles at their open day and average entry, sized by money and merged where they
    crowd each other (the number of traders on or beside each); under the chart the money opened week by week.
    Positions opened before the days shown, or before tracking began, sit in an "earlier" column at the left."""
    import numpy as np
    closes = [b[4] for b in bars]
    start = max(0, len(bars) - show)
    view = bars[start:]
    n = len(view)
    x = np.arange(n)
    ma200 = [sum(closes[i - 199:i + 1]) / 200 if i >= 199 else None for i in range(start, len(bars))]
    blo, bhi = _band_series(bars, start)
    zs = [(t, z) for t, z in (z_hist or []) if z is not None and t >= view[0][0] / 1000]
    with_z = len(zs) >= 10
    last = float(view[-1][4])
    positions = crowd_positions(entries)
    crowd = crowd_summary(positions, last) if positions else None
    v0 = view[0][0] / 1000
    early = bool(crowd) and any(not p.get("since") or p["since"] < v0 for p in positions)

    # the page, in inches from the top
    t_title, t_sub = 0.30, 0.57
    t_crowd = 0.97 if crowd else None
    t_legend = 1.95 if crowd else 0.87
    t_panel = t_legend + 0.20
    p_h = 2.70 if crowd else 3.05
    w_h, w_gap = (0.58, 0.07) if crowd else (0.0, 0.0)
    z_h, z_gap = ((0.98 if crowd else 1.15), 0.09) if with_z else (0.0, 0.0)
    foot = 0.44
    H = t_panel + p_h + w_gap + w_h + z_gap + z_h + foot
    fig = Figure(figsize=(W_IN, H), dpi=DPI)
    _canvas(fig)
    fy = lambda inch: 1 - inch / H  # noqa: E731
    left, right = 0.12, 0.79
    ax = fig.add_axes((left, fy(t_panel + p_h), right - left, p_h / H))
    t_w = t_panel + p_h + w_gap
    cx = fig.add_axes((left, fy(t_w + w_h), right - left, w_h / H)) if crowd else None
    t_z = t_w + w_h + z_gap
    zx = fig.add_axes((left, fy(t_z + z_h), right - left, z_h / H)) if with_z else None
    for a in (ax, cx, zx):
        if a is not None:
            _style(a)

    o, h, low, c = (np.array([b[i] for b in view]) for i in (1, 2, 3, 4))
    up = c >= o
    cols = np.where(up, UP, DOWN)
    bx = [i for i, v in enumerate(blo) if v is not None]
    if bx:                                         # the band under the candles: a violet ribbon with lit edges
        ax.fill_between(bx, [blo[i] for i in bx], [bhi[i] for i in bx], color=BAND, alpha=0.30, linewidth=0,
                        zorder=1)
        for edge in (blo, bhi):
            ax.plot(bx, [edge[i] for i in bx], color=BAND_EDGE, linewidth=0.7, alpha=0.8, zorder=1)
    mx = [i for i, v in enumerate(ma200) if v is not None]
    if mx:
        ax.plot(mx, [ma200[i] for i in mx], color=MA200, linewidth=1.6, linestyle=(0, (4, 1.8)), zorder=2)
    ax.vlines(x, low, h, color=cols, linewidth=0.9, zorder=3)
    span = float(h.max() - low.min()) or 1.0
    ax.bar(x, np.maximum(abs(c - o), span * 0.004), bottom=np.minimum(o, c), color=cols, width=0.72,
           edgecolor=cols, linewidth=0.3, zorder=3)
    pat_note = ""
    pat_items: list[tuple[float, str, str]] = []
    for p in (patterns or [])[:2]:                # Reflex's chart patterns: their shape and break level
        pts = [((t * 1000 - view[0][0]) / DAY_MS, v) for t, v in p.get("anchors") or [] if t and v]
        pts = [(i, v) for i, v in pts if -5 <= i <= n + 5]
        if len(pts) >= 2:
            ax.plot([i for i, _ in pts], [v for _, v in pts], color="#F472B6", linewidth=1.4, alpha=0.95,
                    marker="o", markersize=3.2, zorder=4, path_effects=_glow("#F472B6", 4, 0.25))
        pcol = UP if p.get("direction") == "up" else DOWN if p.get("direction") == "down" else MUTED
        for v in p.get("break_level") or []:
            ax.axhline(v, color=pcol, linewidth=1.0, alpha=0.9, zorder=2)
            pat_items.append((v, f"break {_fmt(v)}", pcol))
        if not pat_note and p.get("pattern"):
            pat_note = f"{p['pattern']} ({p.get('status')})"
    c_lo, c_hi = float(low.min()), float(h.max())
    levels_y = [c_lo, c_hi] + [v for v, _, _ in pat_items]
    recent = n - 60                                             # the band and the 200-day near today stay in view
    levels_y += [v for i, v in enumerate(blo) if v is not None and i >= recent]
    levels_y += [v for i, v in enumerate(bhi) if v is not None and i >= recent]
    levels_y += [v for i, v in enumerate(ma200) if v is not None and i >= recent]
    items: list[tuple[float, str, str]] = [(last, f"now {_fmt(last)}", TEXT)] + pat_items[:2]
    off, nxt = (risk or {}).get("off"), (risk or {}).get("next")
    if off:
        ax.axhline(off["level"], color=OFF, linewidth=1.6, linestyle=(0, (5, 2)), zorder=4)
        items.append((off["level"], f"off {_fmt(off['level'])}", OFF))
        levels_y.append(off["level"])
    if nxt:
        ax.axhline(nxt["level"], color=NEXT, linewidth=1.2, linestyle=(0, (1, 1.8)), zorder=4)
        items.append((nxt["level"], f"next {_fmt(nxt['level'])}", NEXT))
        levels_y.append(nxt["level"])
    if crowd:          # the averages and every dated entry stay in view; earlier ones only within reach of the candles
        levels_y += [crowd[sd]["avg"] for sd in ("long", "short") if crowd[sd]["avg"]]
        reach = 0.35 * (c_hi - c_lo)
        for p in positions:
            if (p.get("since") and p["since"] >= v0) or c_lo - reach <= p["entry"] <= c_hi + reach:
                levels_y.append(p["entry"])
    ax.axhline(last, color=TEXT, linewidth=0.6, alpha=0.35, zorder=2)
    ax.scatter([x[-1]], [last], s=26, color=TEXT, zorder=6, edgecolor=BG_TOP, linewidth=0.8,
               path_effects=_glow(TEXT, 7, 0.25))
    ylo, yhi = min(levels_y), max(levels_y)
    pad = (yhi - ylo) * 0.07
    ylo, yhi = ylo - pad, yhi + pad
    xlo, xhi = -1 - (GUTTER if early else 0), n + 1
    gx = -1 - GUTTER / 2
    if crowd:
        ax_w_pt, ax_h_pt = (right - left) * W_IN * 72, p_h * 72
        ref = max(crowd["long"]["usd"], crowd["short"]["usd"])
        size = lambda v: max(S_MIN, S_MAX * v / ref)  # noqa: E731

        def to_pt(cc):
            yy = min(max(cc["y"], ylo), yhi)
            return (cc["x"] - xlo) / (xhi - xlo) * ax_w_pt, (yy - ylo) / (yhi - ylo) * ax_h_pt

        for a in (ax, cx, zx):
            if a is not None and early:
                a.axvspan(xlo, -1.2, color=BG_TOP, alpha=0.55, linewidth=0, zorder=0.5)
                a.axvline(-1.2, color=MUTED, linewidth=0.7, alpha=0.6, linestyle=(0, (2, 2)), zorder=0.6)
        if early:
            ax.text(gx, 0.985, "earlier", transform=ax.get_xaxis_transform(), color=MUTED, fontsize=8.5,
                    ha="center", va="top", path_effects=_outline())
        drawn = []
        for sd, mk, col in (("long", "^", UP), ("short", "v", DOWN)):
            d = crowd[sd]
            dated = [{"x": min((p["since"] - v0) / 86400, n - 1), "y": p["entry"], "v": p["value"], "n": 1}
                     for p in d["ps"] if p.get("since") and p["since"] >= v0]
            ex = gx - GUTTER * 0.22 if sd == "long" else gx + GUTTER * 0.22
            older = [{"x": ex, "y": p["entry"], "v": p["value"], "n": 1}
                     for p in d["ps"] if not (p.get("since") and p["since"] >= v0)]
            for cc in crowd_clusters(dated, size, to_pt, MAX_CLUSTERS) + crowd_clusters(older, size, to_pt, 1):
                drawn.append((mk, col, cc))
            if d["avg"]:                 # the side's average entry, from the day by which half its money was in
                x0 = (d["since_median"] - v0) / 86400 if d["since_median"] and d["since_median"] >= v0 else xlo
                ax.hlines(d["avg"], x0, xhi, color=col, linewidth=2.0, zorder=4.5, path_effects=_glow(col, 6, 0.22))
                ax.scatter([x0], [d["avg"]], s=22, color=col, zorder=4.6, edgecolor=BG_TOP, linewidth=0.6)
                items.append((d["avg"], f"{'▲' if sd == 'long' else '▼'} avg {_fmt(d['avg'])}", col))
        for mk, col, cc in sorted(drawn, key=lambda t: -t[2]["v"]):     # big first: none hides a small one
            yy = min(max(cc["y"], ylo), yhi)
            s = size(cc["v"])
            ax.scatter([cc["x"]], [yy], s=s, marker=mk, color=col, alpha=0.42, linewidth=0, zorder=5)
            ax.scatter([cc["x"]], [yy], s=s, marker=mk, facecolor="none", edgecolor=col, linewidth=1.3, zorder=5.1)
            side_pt = math.sqrt(s)
            notes = [str(cc["n"])] if cc["n"] > 1 else []
            if yy != cc["y"]:                       # beyond the panel: pinned to its edge, its price written
                notes.append(_fmt(cc["y"]))
            if not notes:
                continue
            if len(notes) == 1 and cc["n"] > 1 and side_pt >= 22:     # the count inside a big triangle
                dy = -side_pt * 0.12 if mk == "^" else side_pt * 0.12
                ax.annotate(notes[0], (cc["x"], yy), xytext=(0, dy), textcoords="offset points", ha="center",
                            va="center", color=TEXT, fontsize=9, fontweight="bold", zorder=6,
                            path_effects=_outline(2.6))
            else:                                   # beside it, on the side with room
                right_side = cc["x"] < n * 0.85
                ax.annotate(" ".join(notes), (cc["x"], yy), xytext=((1 if right_side else -1) * (side_pt / 2 + 2), 0),
                            textcoords="offset points", ha="left" if right_side else "right", va="center",
                            color=col, fontsize=9, fontweight="bold", zorder=6, path_effects=_outline(2.6))
        _crowd_weeks(cx, crowd, n, v0, gx, early)
        cx.set_xlim(xlo, xhi)
    ax.set_ylim(ylo, yhi)
    ax.set_xlim(xlo, xhi)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: _fmt(v)))
    ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(6))
    _labels(ax, items, ylo, yhi)
    ticks = [i for i in range(n % 30, n, 30)]
    fmt = lambda i: dt.datetime.fromtimestamp(view[i][0] / 1000, dt.timezone.utc).strftime("%d %b")  # noqa: E731
    if pat_note:
        ax.text(0.015, 0.03, pat_note, transform=ax.transAxes, color="#F9A8D4", fontsize=9, fontweight="semibold",
                path_effects=_outline())
    if zx is not None:
        zxs = [(t * 1000 - view[0][0]) / DAY_MS for t, _ in zs]
        zlo = min(-1.6, min(z for _, z in zs) - 0.3)
        zhi = max(3.8, max(z for _, z in zs) + 0.3)
        zx.axhspan(2.5, zhi, color=DOWN, alpha=0.10, linewidth=0)           # stretched
        zx.axhspan(zlo, -1.0, color=UP, alpha=0.10, linewidth=0)            # washed out
        for v, col in ((3.5, DOWN), (3.0, DOWN), (2.5, OFF), (-1.0, UP)):
            zx.axhline(v, color=col, linewidth=0.9 if v in (3.0, -1.0) else 0.7, linestyle=(0, (3, 2)),
                       alpha=0.95 if v in (3.0, -1.0) else 0.7)
        zx.plot(zxs, [z for _, z in zs], color=TEXT, linewidth=1.6)
        zx.scatter([zxs[-1]], [zs[-1][1]], s=30, color=TEXT, zorder=4, edgecolor=BG_TOP, linewidth=0.8)
        zx.set_ylim(zlo, zhi)
        zx.set_xlim(xlo, xhi)
        zx.set_yticks([-1.0, 0.0, 2.5, 3.5])                # the lines close together are named on the left
        zx.set_yticklabels(["-1", "0", "2.5", "3.5"])
        for t, col in zip(zx.get_yticklabels(), (UP, MUTED, OFF, DOWN)):
            t.set_color(col)
        _labels(zx, [(3.0, "3 trim", DOWN), (zs[-1][1], f"z {zs[-1][1]:.1f}", TEXT)], zlo, zhi)
    panels = [a for a in (ax, cx, zx) if a is not None]
    for a in panels:
        a.set_xticks(ticks)
        a.tick_params(labelbottom=a is panels[-1])
    panels[-1].set_xticklabels([fmt(i) for i in ticks])

    col = DOWN if side == "short" else UP
    sub = f"now {_fmt(last)}"
    if zs:
        sub += f" · daily z {zs[-1][1]:.1f}"
    if off:
        sub += f" · off {off['pct']:+.1f}%"
    if nxt:
        sub += f" · next {nxt['pct']:+.1f}%"
    fig.text(0.04, fy(t_title), f"{coin} {side} · {strength}", color=col, fontsize=15, fontweight="bold", va="center")
    fig.text(0.04, fy(t_sub), sub, color=MUTED, fontsize=9, va="center")
    fig.text(0.96, fy(H - 0.05), "Outrider", color=MUTED, fontsize=8.5, ha="right", va="bottom", alpha=0.85)
    if crowd:
        _crowd_block(fig, crowd, fy(t_crowd))
    keys = [("fill", BAND, "band"), ("dash", MA200, "200-day"), ("dash", OFF, "off"), ("dot", NEXT, "next")]
    if crowd:
        keys.append(("tri", UP, "traders, size = $" if crowd["sized"] else "traders"))
    _legend(fig, keys, fy(t_legend))
    if entries is not None and not crowd:
        fig.text(0.96, fy(t_legend), "no tracked trader holds it", color=MUTED, fontsize=8.5, ha="right",
                 va="center")
    if early:
        note = f"earlier: opened before {_day(v0)}"
        if any(not p.get("since") for p in positions):
            note += " or before tracking began"
        fig.text(0.04, fy(H - 0.05), note, color=MUTED, fontsize=8.5, ha="left", va="bottom")
    return _png(fig)


def _place_labels(ax, pts: list[tuple[float, float, str, str]], fontsize: float = 9.0) -> None:
    """Coin names beside their points, each moved to the first free spot around its point (no name over another)."""
    fig = ax.get_figure()
    scale = fig.dpi / 72
    taken: list[tuple[float, float, float, float]] = []
    offsets = [(7, 6), (7, -13), (-7, 6), (-7, -13), (0, 12), (0, -18), (12, -3), (-12, -3), (16, 14), (-16, 14),
               (16, -22), (-16, -22)]
    for x, y, name, col in sorted(pts, key=lambda p: -p[1]):
        px, py = ax.transData.transform((x, y))
        w, hgt = 0.62 * fontsize * len(name) * scale, 1.25 * fontsize * scale
        for dx, dy in offsets:
            left = px + dx * scale if dx >= 0 else px + dx * scale - w
            if dx == 0:
                left = px - w / 2
            box = (left, py + dy * scale, left + w, py + dy * scale + hgt)
            if not any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in taken):
                taken.append(box)
                ax.annotate(name, (x, y), xytext=(dx, dy), textcoords="offset points", fontsize=fontsize,
                            fontweight="semibold", color=col, zorder=7, path_effects=_outline(3.2),
                            ha="left" if dx > 0 else "right" if dx < 0 else "center", va="bottom")
                break


def market_map(rows: list[dict], cands: list[dict], label: list[str] = (), title: str | None = None) -> bytes:
    """Every screened coin by where it sits against its band (x, % from the band's middle) and its daily z-score (y),
    with the owner's lines and the corners named; long candidates mint, short coral, the rest dim; size by open
    interest."""
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
    _canvas(fig)
    ax = fig.add_axes((0.12, 0.1, 0.84, 0.73))
    _style(ax)
    ax.set_xlim(-42, 62)
    ax.set_ylim(-2.7, 4.7)
    # the corners that matter: stretched over the band (a fade), under it and washed out
    ax.add_patch(matplotlib.patches.Rectangle((4, 2.5), 58, 2.2, color=DOWN, alpha=0.10, linewidth=0, zorder=0))
    ax.add_patch(matplotlib.patches.Rectangle((-42, -2.7), 38, 1.7, color=UP, alpha=0.10, linewidth=0, zorder=0))
    ax.text(61, 4.55, "stretched over the band", color=DOWN, fontsize=8.5, ha="right", va="top", alpha=0.95,
            path_effects=_outline())
    ax.text(-41, -1.12, "under the band, washed out", color=UP, fontsize=8.5, ha="left", va="top", alpha=0.95,
            path_effects=_outline())
    ax.axvspan(-4, 4, color=BAND, alpha=0.22, linewidth=0, zorder=0)
    ax.text(0, 4.55, "band", color=BAND_EDGE, fontsize=8.5, ha="center", va="top")
    for v, txt, col in ((3.0, "3 trim", DOWN), (2.5, "2.5 blow-off", OFF), (-1.0, "-1 capitulation", UP)):
        ax.axhline(v, color=col, linewidth=0.9, linestyle=(0, (4, 2)), alpha=0.9, zorder=1)
        ax.text(61, v + 0.05, txt, color=col, fontsize=8.5, ha="right", va="bottom", path_effects=_outline())
    want = set(label) | {c["coin"] for c in cands[:8]} | {"BTC", "ETH"}
    extremes = sorted(pts, key=lambda p: -abs(p[2]))[:4]                 # and the most stretched or washed out
    want |= {r["coin"] for r, _, _ in extremes}
    names = []
    for r, x, y in sorted(pts, key=lambda p: p[0]["coin"] in side):
        s = side.get(r["coin"])
        size = 14 + 300 * math.sqrt((r.get("open_interest_usd") or 0) / top_oi)
        col = UP if s == "long" else DOWN if s == "short" else DIM
        if s:
            ax.scatter([x], [y], s=size * 2.6, color=col, alpha=0.18, linewidth=0, zorder=3)   # a halo
        ax.scatter([x], [y], s=size, color=col, alpha=0.95 if s else 0.55,
                   edgecolor="#E6EBFF" if s else "#8C9AD0", linewidth=0.7 if s else 0.3, zorder=4 if s else 2)
        if r["coin"] in want:
            names.append((x, y, r["coin"], col if s else TEXT))
    _place_labels(ax, names)
    ax.set_xlabel("Price against its bull market support band (%)", color=MUTED, fontsize=9)
    ax.set_ylabel("Daily z-score", color=MUTED, fontsize=9)
    n_long = sum(1 for v in side.values() if v == "long")
    _header(fig, title or f"Market map · {len(pts)} coins",
            f"{n_long} long and {len(side) - n_long} short candidates · size: open interest")
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
    _canvas(fig)
    gs = fig.add_gridspec(2, 1, height_ratios=[1.4, 1], hspace=0.12, left=0.12, right=0.95, top=0.84, bottom=0.1)
    ax, mx = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])
    _style(ax)
    _style(mx)
    if beat:
        ax.plot([i for i, _ in beat], [v for _, v in beat], color=OFF, linewidth=2.2, label="beat the market")
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
    _header(fig, f"{label}: {len(got)} bets scored",
            "Meets the declared bar" if met else "Context, not a signal: under the declared bar")
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
    _canvas(fig)
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
        ax.axvspan(max(day(cyc["start"]), t[0]), t[-1], color=BAND, alpha=0.22, linewidth=0)
    ax.plot(t, share, color=TEXT, linewidth=1.8)
    ax.set_xlim(t[0], t[-1])
    if cyc.get("threshold") is not None:
        thr = float(cyc["threshold"])                      # Reflex sends a fraction (0.55); the axis is in percent
        ax.axhline(100 * thr if thr <= 1 else thr, color=MUTED, linewidth=0.9, linestyle="--")
    ax.set_ylim(0, 100)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    btc = [(day(r[0]), r[2]) for r in rows if len(r) > 2 and r[2]]
    if btc:
        bx = ax.twinx()
        bx.plot([a for a, _ in btc], [b for _, b in btc], color=MA200, linewidth=1.0, alpha=0.85)
        bx.set_yscale("log")
        bx.tick_params(which="both", colors=MA200, labelcolor=MA200, labelsize=8.5, length=0)
        bx.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1000:.0f}K"))
        bx.yaxis.set_minor_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1000:.0f}K"))
        for sp in bx.spines.values():
            sp.set_color(BORDER)
    if zx is not None:
        zx.plot([a for a, _ in z], [b for _, b in z], color=OFF, linewidth=1.4)
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
    _header(fig, head, "Share of coins in an uptrend · dashed: the broad-uptrend line · blue: BTC")
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
    _canvas(fig)
    ax = fig.add_axes((0.1, 0.12, 0.86, 0.68))
    _style(ax)
    ax.fill_between(t, over, [a + b for a, b in zip(over, inside)], color=BAND, alpha=0.35, linewidth=0,
                    label="inside the band")
    ax.plot(t, over, color=UP, linewidth=2.0, label="over the band")
    ax.plot([a for a, b in zip(t, o200) if b is not None], [b for b in o200 if b is not None], color=MA200,
            linewidth=1.4, linestyle="--", label="over the 200-day")
    ax.axhline(50, color=MUTED, linewidth=0.8, linestyle=":")
    ax.set_ylim(0, 100)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.legend(loc="upper left", fontsize=8.5, frameon=False, labelcolor=TEXT, ncol=3)
    _header(fig, f"Breadth · {days[-1]['coins']} coins", "Today's most traded coins, rebuilt from their candles",
            y=0.94)
    return _png(fig)
