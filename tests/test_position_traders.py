import asyncio

from synth import bot, notify, service
from synth.config import Settings
from synth.position_traders import PositionTraders, conviction_text
from synth.store import Store

A = "0x5f94a51948d2376ad34a6fadfa2544e651b74b96"
SEED = {"as_of": "2026-10-10", "rule": "S3", "traders": [{
    "address": A, "swing_positions": 28, "swing_pnl": 2_035_583, "profit_factor": 2.25, "win_rate": 0.536,
    "median_hold_days": 50.5, "median_size": 500_000, "long_share": 0.27,
    "best": {"coin": "TRUMP", "side": "short", "pnl": 1_215_311, "opened": "2025-10-27", "closed": "2026-08-22"},
    "open_since": {"LIT:long": 1_780_000_000.0}}]}


class FakeHL:
    def __init__(self):
        self.book = {"LIT": {"side": "long", "size": 1, "entry_px": 1.0, "notional": 2_600_000, "upnl": 1_500_000}}
        self.equity = 2_500_000

    async def account(self, address):
        return {"equity": self.equity, "positions": {k: dict(v) for k, v in self.book.items()}}


def setup(tmp_path, monkeypatch):
    sent = []

    async def fake(token, chat, text, buttons=None):
        sent.append(text)

    monkeypatch.setattr(notify, "telegram", fake)
    s = Settings(db_path=str(tmp_path / "t.db"), telegram_token="t", telegram_chat_id="1")
    store = Store(s.db_path)
    hl = FakeHL()
    return PositionTraders(s, store, seed=SEED, hl=hl), store, hl, sent


def test_first_look_is_the_starting_point_then_new_positions_and_conviction(tmp_path, monkeypatch):
    pt, store, hl, sent = setup(tmp_path, monkeypatch)
    out = asyncio.run(pt.step(now=1_790_000_000.0))
    assert out == {"pt_read": 1, "pt_new": 0, "pt_closed": 0, "pt_conviction": 0} and not sent
    b = pt.board(now=1_790_000_000.0)
    lit = b["traders"][0]["positions"][0]
    assert lit["coin"] == "LIT" and lit["held_days"] == round(10_000_000 / 86400, 1) and not lit["new"]
    assert b["traders"][0]["leverage"] == 1.04 and b["traders"][0]["moderate"]
    assert not store.picks(origin="position")

    # a small new short: logged, no alert
    hl.book["ZEC"] = {"side": "short", "size": 1, "entry_px": 300.0, "notional": 200_000, "upnl": 0}
    out = asyncio.run(pt.step(now=1_790_000_300.0, mids={"ZEC": 301.0}))
    assert out["pt_new"] == 1 and out["pt_conviction"] == 0 and not sent
    p = store.picks(origin="position")[0]
    assert (p["coin"], p["direction"], p["entry_price"], p["conviction"]) == ("ZEC", "short", 301.0, 1)

    # it grows past 2x their usual size within 4 hours: one conviction alert
    hl.book["ZEC"]["notional"] = 1_200_000
    out = asyncio.run(pt.step(now=1_790_003_600.0))
    assert out["pt_conviction"] == 1 and len(sent) == 1
    assert sent[0].startswith("Conviction · ZEC short $1.2M (2.4x their usual)")
    assert store.picks(origin="position")[0]["conviction"] == 2
    asyncio.run(pt.step(now=1_790_007_200.0))
    assert len(sent) == 1                                   # not twice

    # a flip closes one and opens another; a coin gone is closed
    hl.book["ZEC"] = {"side": "long", "size": 1, "entry_px": 290.0, "notional": 50_000, "upnl": 0}
    del hl.book["LIT"]
    out = asyncio.run(pt.step(now=1_790_100_000.0))
    assert out["pt_closed"] == 2 and out["pt_new"] == 1
    assert [p["coin"] + " " + p["side"] for p in pt.board()["traders"][0]["positions"]] == ["ZEC long"]
    assert "ZEC 1 long $50K / 0 short $0" in pt.coins_text()


def test_conviction_text_and_bot_command(tmp_path, monkeypatch):
    text = conviction_text(SEED["traders"][0], {"coin": "LIT", "side": "long", "value": 2_642_000}, 6.4)
    assert text.splitlines()[0] == "Conviction · LIT long $2.6M (5.3x their usual)"
    assert text.splitlines()[1] == ("0x5f94…4b96 · 28 swing trades, 54% won, +$2.0M, PF 2.25, holds ~50d "
                                    "· 6.4x lev now")
    pt, store, hl, sent = setup(tmp_path, monkeypatch)
    asyncio.run(pt.step(now=1_790_000_000.0))

    class Agent:
        s = pt.s
        store = pt.store
        positions = pt

    asyncio.run(bot.handle(Agent(), {"message": {"chat": {"id": 1}, "text": "/positions"}}))
    assert sent[-1].splitlines()[1].startswith("LIT 1 long $2.6M / 0 short $0")


def test_position_picks_score_at_14_and_30_days_never_late(tmp_path):
    s = Settings(db_path=str(tmp_path / "t.db"))
    store = Store(s.db_path)
    a = service.Agent(s, store)
    a.tracker.last_mids = {"ZEC": 110.0}
    pid = store.add_pick("position", "ZEC", "long", 1, 100.0, "pt", {})
    late = store.add_pick("position", "ZEC", "long", 1, 100.0, "pt", {})
    import time
    with store._lock:
        store._db.execute("UPDATE picks SET ts=? WHERE id=?", (time.time() - 30 * 86400 - 3600, pid))
        store._db.execute("UPDATE picks SET ts=? WHERE id=?", (time.time() - 30 * 86400 - 2 * 86400, late))
        store._db.commit()
    a.score_picks({})
    got = {p["id"]: p for p in store.picks(origin="position")}
    assert got[pid]["ret_30d"] == 0.1 and got[pid]["ret_14d"] is None   # 14d was due long ago: left empty
    assert got[late]["ret_30d"] is None                                 # missed its day: never scored late
    assert got[pid]["ret_7d"] == 0.1                                    # older horizons as before
