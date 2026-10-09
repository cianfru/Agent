"""Group chart for Telegram: 48h of price, each trader's entry, and where they all start losing.

Pure function of its inputs; returns PNG bytes. matplotlib with the Agg backend (no display).
"""

from __future__ import annotations

import datetime as dt
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
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
