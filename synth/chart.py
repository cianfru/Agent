"""Group chart for Telegram: 48h of price, each trader's entry, and where they all start losing.

Pure function of its inputs; returns PNG bytes. matplotlib with the Agg backend (no display).
"""

from __future__ import annotations

import datetime as dt
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.patches  # noqa: E402,F401
import matplotlib.ticker  # noqa: E402,F401
import matplotlib.pyplot as plt  # noqa: E402

GROUND, SURFACE, BORDER = "#0e0d0b", "#161412", "#3b352f"
TEXT, MUTED = "#f7f3ed", "#8f877c"
UP, DOWN, ACCENT = "#9fe6c7", "#f4968b", "#ff5e00"


def _fmt(x: float) -> str:
    return f"{x:,.0f}" if x >= 1000 else f"{x:.2f}" if x >= 1 else f"{x:.4g}"


def group_chart(coin: str, side: str, candles: list[list[float]], members: list[dict],
                all_losing: float | None, title: str) -> bytes:
    """candles: [[t_ms, o, h, l, c]]; members: [{entry_px, opened_ts, status}]."""
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
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
    plt.close(fig)
    return buf.getvalue()


def _usd(x: float) -> str:
    a = abs(x)
    s = f"${a / 1e6:.1f}M" if a >= 1e6 else f"${a / 1e3:.0f}K" if a >= 1e3 else f"${a:.0f}"
    return ("+" if x > 0 else "−" if x < 0 else "") + s


def _signed(n: int) -> str:
    return f"+{n}" if n > 0 else f"−{-n}" if n < 0 else "0"


def pulse_chart(points: list[dict], grid: dict, now: float, title: str = "Smart money pulse · Hyperliquid") -> bytes:
    """The live pulse for Telegram: share of profitable traders' positions that are long over 24h, and the coins
    with the most movement, cell by cell for the last 1h, 6h and 24h. Pure; returns PNG bytes."""
    coins = grid["coins"]
    rows_h = 0.42 * max(len(coins), 3) + 0.5
    height = 1.0 + 2.2 + rows_h + 0.55
    fig = plt.figure(figsize=(8, height), dpi=150)
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
    plt.close(fig)
    return buf.getvalue()


WINDOW_KEYS = (1, 6, 24)
