import asyncio
import time

from synth import bot, notify, service
from synth.config import Settings
from synth.position_traders import (PositionTraders, build_text, conviction_text, leaderboard_candidates,
                                    leverage_band, record_line, ride_qualifies)
from synth.store import Store

A = "0x5f94a51948d2376ad34a6fadfa2544e651b74b96"
PROVEN = {"address": A, "tier": "proven", "swing_positions": 28, "swing_pnl": 2_035_583, "realized_swing_pnl": 1_500_000,
          "open_swing_pnl": 535_583, "profit_factor": 2.25, "win_rate": 0.536, "median_hold_days": 50.5,
          "median_size": 500_000, "long_share": 0.27,
          "best": {"coin": "TRUMP", "side": "short", "pnl": 1_215_311, "opened": "2025-10-27", "closed": "2026-08-22"},
          "open_since": {"LIT:long": 1_780_000_000.0}}
SEED = {"as_of": "2026-10-10", "rule": {}, "traders": [PROVEN]}


def pos(side, notional, size=1.0, upnl=0.0, entry=1.0):
    return {"side": side, "size": size, "entry_px": entry, "notional": notional, "upnl": upnl}


class FakeHL:
    def __init__(self, books=None):
        self.books = books or {A: {"LIT": pos("long", 2_600_000, 1_000_000, 1_500_000)}}
        self.equity = 2_500_000

    async def account(self, address):
        return {"equity": self.equity, "positions": {k: dict(v) for k, v in self.books.get(address, {}).items()}}


def setup(tmp_path, monkeypatch, books=None):
    sent = []

    async def fake(token, chat, text, buttons=None):
        sent.append(text)

    monkeypatch.setattr(notify, "telegram", fake)
    s = Settings(db_path=str(tmp_path / "t.db"), telegram_token="t", telegram_chat_id="1")
    store = Store(s.db_path)
    hl = FakeHL(books)
    return PositionTraders(s, store, seed=SEED, hl=hl), store, hl, sent


def test_first_look_is_the_starting_point_then_adds_new_positions_and_conviction(tmp_path, monkeypatch):
    pt, store, hl, sent = setup(tmp_path, monkeypatch)
    out = asyncio.run(pt.step(now=1_790_000_000.0))
    assert (out["pt_read"], out["pt_new"], out["pt_conviction"]) == (1, 0, 0) and not sent
    b = pt.board(now=1_790_000_000.0)
    t = b["traders"][0]
    assert t["positions"][0]["held_days"] == round(10_000_000 / 86400, 1) and not t["positions"][0]["new"]
    assert (t["leverage"], t["leverage_band"], t["tier"]) == (1.04, "low", "proven")
    assert not store.picks(origin="position") and not store.pt_flow_since(0)

    # adding to LIT: a flow of +$520K (200,000 more at the mid)
    hl.books[A]["LIT"] = pos("long", 3_120_000, 1_200_000, 1_500_000)
    asyncio.run(pt.step(now=1_790_000_300.0, mids={"LIT": 2.6}))
    assert [round(f["usd"]) for f in store.pt_flow_since(0)] == [520_000]

    # a small new short: logged and a flow, no alert
    hl.books[A]["ZEC"] = pos("short", 200_000, 666, entry=300.0)
    out = asyncio.run(pt.step(now=1_790_000_600.0, mids={"ZEC": 301.0, "LIT": 2.6}))
    assert out["pt_new"] == 1 and out["pt_conviction"] == 0 and not sent
    p = store.picks(origin="position")[0]
    assert (p["coin"], p["direction"], p["entry_price"], p["conviction"]) == ("ZEC", "short", 301.0, 1)

    # it grows past 2x their usual size within 4 hours: one conviction alert
    hl.books[A]["ZEC"] = pos("short", 1_200_000, 4_000, entry=300.0)
    out = asyncio.run(pt.step(now=1_790_003_600.0, mids={"ZEC": 300.0, "LIT": 2.6}))
    assert out["pt_conviction"] == 1 and len(sent) == 1
    assert sent[0].startswith("Conviction · ZEC short $1.2M (2.4x their usual)")
    assert store.picks(origin="position")[0]["conviction"] == 2
    asyncio.run(pt.step(now=1_790_007_200.0, mids={"ZEC": 300.0, "LIT": 2.6}))
    assert len(sent) == 1                                   # not twice

    # a flip closes one and opens another; a coin gone is closed (cuts recorded)
    hl.books[A]["ZEC"] = pos("long", 50_000, 166, entry=290.0)
    del hl.books[A]["LIT"]
    out = asyncio.run(pt.step(now=1_790_100_000.0, mids={"ZEC": 300.0, "LIT": 2.6}))
    assert out["pt_closed"] == 2 and out["pt_new"] == 1
    assert [p["coin"] + " " + p["side"] for p in pt.board()["traders"][0]["positions"]] == ["ZEC long"]
    adding = {(r["coin"], r["side"]): r for r in pt.adding(48, now=1_790_100_000.0)}
    assert adding[("LIT", "long")]["cut_usd"] == 2_600_000   # net per trader: +520K then -3.12M and adding[("ZEC", "long")]["added_usd"] == 50_000
    assert "ZEC 1 long $50K / 0 short $0" in pt.coins_text()


def test_build_up_alert_when_three_traders_add_to_one_coin_and_price_is_flat(tmp_path, monkeypatch):
    B, C = "0x" + "b" * 40, "0x" + "c" * 40
    pt, store, hl, sent = setup(tmp_path, monkeypatch, books={A: {}, B: {}, C: {}})
    pt.seed_traders.update({B: {**PROVEN, "address": B}, C: {**PROVEN, "address": C}})
    now = 1_790_000_000.0
    store.add_series("reflex", {"w": 600, "stale": False, "c": {"GRASS": [0, 0, 0, 0, 0.60]}}, ts=now - 72 * 3600 + 600)
    asyncio.run(pt.step(now=now, mids={"GRASS": 0.62}))               # first look: empty books
    for i, a in enumerate((A, B, C)):
        hl.books[a]["GRASS"] = pos("long", 120_000, 200_000, entry=0.61)
        asyncio.run(pt.step(now=now + 600 * (i + 1), mids={"GRASS": 0.62}))
    builds = [m for m in sent if m.startswith("Building")]
    assert builds == ["Building · GRASS long: 3 tracked traders added $360K in 3 days · price +3% · they hold "
                      "$360K long\nAgainst: $0 short among tracked traders\nContext, not a signal."]
    asyncio.run(pt.step(now=now + 3600, mids={"GRASS": 0.62}))
    assert len([m for m in sent if m.startswith("Building")]) == 1    # cooldown


def test_riders_found_daily_and_the_rule():
    st = {"equity": 2_000_000, "positions": {"NEAR": pos("long", 3_800_000, upnl=1_970_000),
                                             "XPL": pos("long", 620_000, upnl=-98_000)}}
    q = ride_qualifies(st, {"alltime_pnl": 4_400_000, "av": 4_200_000, "month_vlm": 4_700_000, "month_pnl": 1_800_000})
    assert q["tier"] == "riding" and q["open_pnl"] == 1_872_000 and q["best"]["coin"] == "NEAR"
    assert ride_qualifies(st, {"alltime_pnl": -1}) is None                                   # lost money all-time
    assert ride_qualifies(st, {"alltime_pnl": 1, "av": 1e6, "month_vlm": 2e8}) is None      # market maker
    small = {"equity": 1, "positions": {"X": pos("long", 1_000_000, upnl=100_000)}}
    assert ride_qualifies(small, {"alltime_pnl": 1}) is None                                 # under $250K open
    rows = [{"ethAddress": "0xAAA", "accountValue": "600000", "windowPerformances": [
        ["month", {"pnl": "1", "vlm": "1000"}], ["allTime", {"pnl": "5", "vlm": "1"}]]},
            {"ethAddress": "0xbbb", "accountValue": "100000", "windowPerformances": [
        ["month", {"pnl": "1", "vlm": "1"}], ["allTime", {"pnl": "5", "vlm": "1"}]]}]
    assert list(leaderboard_candidates(rows)) == ["0xaaa"]


def test_discover_adds_riders_and_prunes(tmp_path, monkeypatch):
    R = "0x" + "d" * 40
    pt, store, hl, sent = setup(tmp_path, monkeypatch, books={R: {"NEAR": pos("long", 3_800_000, upnl=1_970_000)}})
    rows = [{"ethAddress": R, "accountValue": "4200000", "windowPerformances": [
        ["month", {"pnl": "1800000", "vlm": "4700000"}], ["allTime", {"pnl": "4400000", "vlm": "1"}]]}]
    out = asyncio.run(pt.discover(now=1_790_000_000.0, rows=rows, hl=hl))
    assert out == {"candidates": 1, "riding": 1, "dropped": 0} and R in pt.traders and A in pt.traders
    assert pt.big_money() == {"NEAR": {"long_n": 1, "short_n": 0, "long_usd": 3_800_000, "short_usd": 0}}
    assert not pt.discover_due(now=1_790_000_000.0 + 3600)
    asyncio.run(pt.step(now=1_790_000_100.0))
    t = next(x for x in pt.board()["traders"] if x["address"] == R)
    assert t["tier"] == "riding" and t["record_text"] == "riding · +$2.0M open profit, all-time +$4.4M"
    hl.books[R] = {}
    out = asyncio.run(pt.discover(now=1_790_000_000.0 + 15 * 86400, rows=rows, hl=hl))
    assert out["dropped"] == 1 and R not in pt.traders


def test_texts_and_bot_command(tmp_path, monkeypatch):
    assert record_line(PROVEN) == ("proven · 28 held 3d+, 54% won, +$2.0M ($536K on paper), PF 2.25, holds ~50d")
    assert [leverage_band(x) for x in (None, 0.2, 3, 5, 7.5)] == [None, "low", "moderate", "moderate", "high"]
    text = conviction_text(PROVEN, {"coin": "LIT", "side": "long", "value": 2_642_000}, 6.4)
    assert text.splitlines()[0] == "Conviction · LIT long $2.6M (5.3x their usual)"
    assert build_text({"coin": "ZRO", "side": "long", "traders": 4, "added_usd": 900_000, "move": None}).startswith(
        "Building · ZRO long: 4 tracked traders added $900K in 3 days · price n/a")
    xpl = build_text({"coin": "XPL", "side": "long", "traders": 3, "added_usd": 400_000, "move": -0.05,
                      "held_usd": 3_810_000, "against_usd": 1_360_000,
                      "big_money": {"long_n": 22, "short_n": 23, "long_usd": 7_390_000, "short_usd": 18_430_000}})
    assert xpl.splitlines()[1:] == ["Against: $1.4M short among tracked traders",
                                    "Big money (daily): $7.4M long / $18.4M short, leans the other way",
                                    "Context, not a signal."]
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
    with store._lock:
        store._db.execute("UPDATE picks SET ts=? WHERE id=?", (time.time() - 30 * 86400 - 3600, pid))
        store._db.execute("UPDATE picks SET ts=? WHERE id=?", (time.time() - 30 * 86400 - 2 * 86400, late))
        store._db.commit()
    a.score_picks({})
    got = {p["id"]: p for p in store.picks(origin="position")}
    assert got[pid]["ret_30d"] == 0.1 and got[pid]["ret_14d"] is None   # 14d was due long ago: left empty
    assert got[late]["ret_30d"] is None                                 # missed its day: never scored late
    assert got[pid]["ret_7d"] == 0.1                                    # older horizons as before
