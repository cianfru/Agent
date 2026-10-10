import asyncio

from synth import bot, chart, flow, notify
from synth.config import Settings
from synth.store import Store

NOW = 100_000.0


def row(c, w=600, stale=False):
    return {"w": w, "stale": stale, "c": c}


def test_record_from_snapshot():
    assert flow.record({"positions": {"BTC": [1, 2, 3, 4, 5.0]}, "tracked_wallets": 600}) == \
        row({"BTC": [1, 2, 3, 4, 5.0]})
    assert flow.record({"coins": {}}) is None and flow.record(None) is None


def test_window_counts_opens_and_closes_and_takes_out_the_price_move():
    rows = [
        (NOW - 3700, row({"BTC": [10, 1_000_000, 20, 2_000_000, 100.0]})),            # just before the 1h window
        (NOW - 3000, row({"BTC": [11, 1_200_000, 20, 2_000_000, 100.0]})),            # a long opened ($200K)
        (NOW - 1800, row({"BTC": [11, 1_320_000, 19, 2_090_000, 110.0],                # price +10%, one short closed
                          "ETH": [0, 0, 1, 60_000, 2000.0]})),                        # a short opened on ETH
    ]
    w = flow.window(rows, 1, NOW)
    btc = next(c for c in w["coins"] if c["coin"] == "BTC")
    assert (btc["d_long_n"], btc["d_short_n"], btc["shift"]) == (1, -1, 2)
    # longs: +200K, then 1.32M vs 1.2M x 1.1 = 0 -> price move only; shorts: 2.09M vs 2.0M x 1.1 = -110K
    assert btc["d_long_usd"] == 200_000 and btc["d_short_usd"] == -110_000 and btc["net_usd"] == 310_000
    eth = next(c for c in w["coins"] if c["coin"] == "ETH")
    assert eth["shift"] == -1 and eth["net_usd"] == -60_000
    t = w["totals"]
    assert (t["d_long_n"], t["d_short_n"], t["long_n"], t["short_n"]) == (1, 0, 11, 20)
    assert t["long_share"] == 35.5 and w["coins"][0]["coin"] == "BTC"


def test_window_skips_roster_changes_gaps_and_stale_data():
    rows = [
        (NOW - 3500, row({"SOL": [5, 0, 5, 0, None]})),
        (NOW - 3200, row({"SOL": [9, 0, 5, 0, None]}, w=650)),    # roster refresh: 4 wallets added, not trades
        (NOW - 2900, row({"SOL": [10, 0, 5, 0, None]}, w=650)),   # one real open
        (NOW - 100, row({"SOL": [12, 0, 5, 0, None]}, w=650)),    # 47 min gap: unknown, left out
        (NOW - 50, row({"SOL": [20, 0, 5, 0, None]}, w=650, stale=True)),
    ]
    w = flow.window(rows, 1, NOW)
    assert w["coins"][0]["d_long_n"] == 1 and w["skipped_intervals"] == 3
    assert w["covered_h"] == round(300 / 3600, 2)


def test_grid_series_summary_and_picture():
    rows = [(NOW - 86_000 + i * 300, row({"BTC": [10 + (i > 280), 0, 20, 0, 1.0], "PUMP": [3, 0, 5 + (i > 200), 0, 1.0]}))
            for i in range(287)]
    g = flow.grid(rows, NOW)
    assert [c["coin"] for c in g["coins"]] == ["BTC", "PUMP"]
    assert g["coins"][0]["1h"]["shift"] == 1 and g["coins"][1]["1h"] is None and g["coins"][1]["24h"]["shift"] == -1
    pts = flow.series(rows, 24, NOW)
    assert pts[-1]["long_n"] == 14 and pts[0]["long_share"] == round(100 * 13 / 38, 2)
    text = flow.summary(g["windows"])
    assert text.splitlines()[0] == "Smart money: 14 long · 26 short (35.0% long)"
    assert "1h: longs +1, shorts +0 · moving: BTC long" in text
    png = chart.pulse_chart(pts, g, NOW)
    assert png[:4] == b"\x89PNG"
    assert chart.pulse_chart([], flow.grid([], NOW), NOW)[:4] == b"\x89PNG"  # empty: still a picture


def test_store_series_roundtrip_and_prune(tmp_path):
    st = Store(str(tmp_path / "t.db"))
    st.add_series("reflex", row({"BTC": [1, 0, 0, 0, 1.0]}), ts=10.0)
    st.add_series("reflex", row({"BTC": [2, 0, 0, 0, 1.0]}), ts=20.0)
    assert [t for t, _ in st.series("reflex", 15)] == [20.0]
    st.prune_series(15)
    assert len(st.series("reflex", 0)) == 1


def test_now_command_sends_the_picture(tmp_path, monkeypatch):
    sent = []

    async def photo(token, chat, png, caption, buttons=None):
        sent.append((png[:4], caption))

    monkeypatch.setattr(notify, "telegram_photo", photo)

    class A:
        s = Settings(db_path=str(tmp_path / "t.db"), telegram_token="t", telegram_chat_id="1")
        store = Store(s.db_path)

        def pulse(self):
            return {"png": b"\x89PNGx", "summary": "Smart money: 1 long · 2 short (33.3% long)"}

    asyncio.run(bot.handle(A(), {"message": {"chat": {"id": 1}, "text": "/now@theOutriderbot"}}))
    assert sent == [(b"\x89PNG", "Smart money: 1 long · 2 short (33.3% long)")]
