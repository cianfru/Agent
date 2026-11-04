"""Pictures at no cost, sized for a phone, sent as one message; and the read's board and header built in code."""

import asyncio
import io
import math
import random
import threading
import time

from PIL import Image

from synth import analyst, chart, notify
from synth.config import Settings
from synth.store import Store

DAY = 86_400_000


def bars(n=400, seed=1):
    random.seed(seed)
    now = int(time.time() * 1000) // DAY * DAY
    out, p = [], 100.0
    for i in range(n):
        o = p
        p *= 1 + random.gauss(0.001, 0.03)
        out.append([now - (n - 1 - i) * DAY, o, max(o, p) * 1.01, min(o, p) * 0.99, p, 1000.0])
    return out


def size(png):
    return Image.open(io.BytesIO(png)).size


def test_charts_fit_a_phone_and_telegram():
    b = bars()
    risk = {"off": {"level": b[-1][4] * 0.95, "name": "band low", "pct": -5.0},
            "next": {"level": b[-1][4] * 1.1, "name": "swing high", "pct": 10.0}}
    z = [((b[-1][0] - (59 - i) * DAY) / 1000, 1 + math.sin(i / 9) * 1.8) for i in range(60)]
    for png in (chart.setup_chart("SOL", "long", "firm", b, risk, z, [((b[-6][0]) / 1000, b[-6][4], "long")]),
                chart.setup_chart("SOL", "short", "light", b[-80:], None, z[:3]),           # short history, no z panel
                chart.market_map([{"coin": f"C{i}", "price": 100 + i, "band": {"low": 100, "high": 105},
                                   "z_1d": i / 20 - 1, "open_interest_usd": 1e6 * i} for i in range(60)],
                                 [{"coin": "C5", "side": "long"}], ["C9"])):
        w, h = size(png)
        assert w == 1200 and h <= 1280                    # Telegram does not shrink it; text stays readable


def test_the_record_chart_waits_for_a_scored_setup():
    assert chart.journal_chart([], {"n": 60, "right_pct": 62, "median_pct": 1.0}, 0) is None
    t0 = time.time() - 30 * 86400
    picks = [{"ts": t0 + i * 86400, "ret_3d": (-1) ** i * 0.02, "mkt_3d": 0.0, "direction": "long", "coin": f"C{i}"}
             for i in range(9)]
    png = chart.journal_chart(picks, {"n": 60, "right_pct": 62, "median_pct": 1.0}, t0)
    assert size(png)[0] == 1200


def test_renders_in_two_threads_do_not_mix():
    out = []
    b = bars()

    def run():
        out.append(chart.setup_chart("SOL", "long", "firm", b))

    ts = [threading.Thread(target=run) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(out) == 3 and all(size(p)[0] == 1200 for p in out)


def test_pictures_go_with_the_text_as_one_counted_message(tmp_path, monkeypatch):
    s = Settings(db_path=str(tmp_path / "t.db"), telegram_token="t", telegram_chat_id="1")
    store = Store(s.db_path)
    calls = []

    async def fake_tg(token, method, http_timeout=15, files=None, **params):
        calls.append((method, len(files or {}), params.get("caption", ""), params.get("disable_notification")))
        return [{"message_id": 1}] if method == "sendMediaGroup" else {"message_id": 1}

    monkeypatch.setattr(notify, "tg", fake_tg)
    asyncio.run(notify.push(s, store, "short read", "read", pngs=[b"a", b"b"]))
    assert calls == [("sendMediaGroup", 2, "", None)] or calls[0][0] == "sendMediaGroup"
    calls.clear()
    asyncio.run(notify.push(s, store, "x" * 2000, "read", pngs=[b"a", b"b"]))         # too long for a caption
    assert [c[0] for c in calls] == ["sendMessage", "sendMediaGroup"] and calls[1][3] is True   # pictures silent
    assert store.count_briefs("push", time.time() - 60) == 2                          # one message each time


def test_the_board_and_header_are_built_in_code():
    cur = {"market": {"turns": {"state": "broad uptrend", "run_day": 12, "longer_than": "7 of 13 past broad uptrends"},
                      "breadth": {"below": 47, "inside": 12, "just above": 20, "above": 15, "extended": 6,
                                  "over_200d": 38, "with_200d": 96},
                      "z_extremes": {"stretched": [["ENA", 2.8]], "washed_out": []}},
           "direction": [{"coin": "BTC", "price": 104_200.0, "change_24h_pct": 1.2, "screen": {"band": "just above"},
                          "engine": {"z_daily": 1.1}}]}
    assert analyst.board_text(cur).splitlines() == [
        "Broad uptrend · day 12 (longer than 7 of 13)",
        "BTC 104,200 +1.2% · just above its band · z 1.1",
        "Breadth: 41 over the band · 12 inside · 47 under · 38 of 96 over the 200-day",
        "Stretched (z 2.5+): ENA 2.8 · Washed out (z -1 or lower): none"]
    now = time.mktime(time.strptime("2026-11-03 14:05", "%Y-%m-%d %H:%M")) - time.timezone
    ch = [{"w": 3, "text": "BTC -4.2% since the last read", "owner": False}, {"w": 4, "text": "yours", "owner": True}]
    assert analyst.header(now, "market changed", {"market_lean": "short"}, ch) == (
        "Outrider · 14:05 UTC · market changed: BTC -4.2% since the last read · leaning short")
    assert analyst.header(now, "morning", {"market_lean": "neutral"}) == "Outrider · 14:05 UTC · morning"
