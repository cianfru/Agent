import asyncio

from synth import groups as G
from synth import notify, tracking
from synth.config import Settings
from synth.store import Store

GROUP = {"id": 7, "venue": "hyperliquid", "coin": "STRK", "side": "short", "meta": {"span_h": 3.0},
         "ref_px": 0.0739, "followed": False}
MEMBERS = [
    {"address": "0xaaaa000000000000000000000000000000001111", "entry_px": 0.0712, "size_usd": 42_000, "opened_ts": 0},
    {"address": "0xbbbb000000000000000000000000000000002222", "entry_px": 0.0743, "size_usd": 18_000, "opened_ts": 0},
    {"address": "0xcccc000000000000000000000000000000003333", "entry_px": 0.0587, "size_usd": 9_000, "opened_ts": 0},
]


def test_levels_and_caption_explain_the_line():
    lv = G.levels(GROUP, MEMBERS)
    assert (lv["lo"], lv["hi"], lv["all_losing"]) == (0.0587, 0.0743, 0.0743)
    text = G.caption(GROUP, MEMBERS, 0.0732)
    assert text.splitlines()[0] == "#7 STRK short · 3 profitable traders in 3h"
    assert "0xaaaa…1111  $42K @ 0.0712" in text
    assert "Entries 0.0587–0.0743 · now 0.0732" in text
    assert "Above 0.0743 every short here is losing." in text
    longs = {**GROUP, "side": "long"}
    assert "Below 0.0587 every long here is losing." in G.caption(longs, MEMBERS, 0.07)


def test_hl_update_open_reduced_closed():
    m = {"status": "open", "entry_px": 0.0712, "size": 1000, "opened_ts": 0}
    upd, ch = G.hl_update(GROUP, m, {"side": "short", "size": 900}, None, 0.07, 100)
    assert ch is None and upd["last_px"] == 0.07
    upd, ch = G.hl_update(GROUP, m, {"side": "short", "size": 300}, None, 0.07, 100)
    assert ch == "reduced" and upd["status"] == "reduced"
    fills = [{"time": 50_000, "px": 0.066, "sz": 600, "pnl": 30.0}, {"time": 60_000, "px": 0.064, "sz": 400, "pnl": 25.0}]
    upd, ch = G.hl_update(GROUP, m, None, fills, 0.063, 100)
    assert ch == "closed" and upd["approx"] == 0 and upd["exit_ts"] == 60
    assert round(upd["exit_px"], 4) == 0.0652 and round(upd["pnl_pct"], 3) == 0.084 and upd["pnl_usd"] == 55.0
    upd, ch = G.hl_update(GROUP, m, {"side": "long", "size": 5}, [], 0.08, 100)  # flipped, no fills found
    assert ch == "closed" and upd["approx"] == 1 and upd["pnl_pct"] < 0


def test_derive_update_closed_and_expired():
    m = {"status": "open", "entry_px": 2000, "size": 2.0, "expiry": 1000, "last_px": 2500}
    upd, ch = G.derive_update(m, {"net": 2.0, "mark": 2600}, 500)
    assert ch is None and upd["last_px"] == 2600
    upd, ch = G.derive_update(m, None, 500)
    assert ch == "closed" and upd["pnl_pct"] == 0.25 and upd["pnl_usd"] == 1000 and upd["approx"] == 1
    upd, ch = G.derive_update(m, None, 2000)
    assert ch == "expired"


def test_derive_opens_detects_new_smart_legs_only():
    leg = {"instrument": "BTC-20261030-85000-C", "net": 10, "entry": 2000, "mark": 2100, "delta_usd": 300_000,
           "expiry": 1793347200}
    prev = {"wallets": {"BTC": [{"address": "0xa", "smart": True, "legs": []},
                                {"address": "0xb", "smart": False, "legs": []}]}}
    cur = {"wallets": {"BTC": [{"address": "0xa", "smart": True, "legs": [leg]},
                               {"address": "0xb", "smart": False, "legs": [leg]}]}}
    (o,) = G.derive_opens(prev, cur, now=5)
    assert (o["address"], o["side"], o["entry_px"], o["opened_ts"]) == ("0xa", "long", 2000, 5)
    assert G.derive_opens(None, cur, now=5) == []


def test_scoreboard():
    groups = [{"id": 1, "status": "closed"}, {"id": 2, "status": "closed"}, {"id": 3, "status": "open"}]
    members = {1: [{"pnl_pct": 0.05}, {"pnl_pct": 0.01}], 2: [{"pnl_pct": -0.04}]}
    sb = G.scoreboard(groups, members)
    assert {**sb, "avg_closed_pct": round(sb["avg_closed_pct"], 6)} == {
        "groups": 3, "open": 1, "closed": 2, "closed_in_profit": 1, "avg_closed_pct": -0.005}


# ------------------------------------------------------- end to end, offline --


class FakeReflex:
    async def get(self, path):
        assert path.startswith("/api/hyperlens/opens/STRK")
        return {"convergences": [{"side": "short", "first_ts": 1000.0, "n": 2, "wallets": [
            {"address": "0xA1", "ts": 1000.0, "entry_px": 0.0712, "size_usd": 42_000},
            {"address": "0xB2", "ts": 1900.0, "entry_px": 0.0743, "size_usd": 18_000},
        ]}]}


class FakeHL:
    def __init__(self):
        self.closed = set()

    async def mids(self):
        return {"STRK": 0.0700}

    async def positions(self, address):
        return {} if address in self.closed else {"STRK": {"side": "short", "size": 500_000}}

    async def closing_fills(self, address, coin, since_ms):
        return [{"time": 9_000_000, "px": 0.0650, "sz": 590_000, "pnl": 3600.0}]

    async def candles(self, *a):
        return [[0, 0.07, 0.071, 0.069, 0.0705]]


def test_group_announced_followed_and_close_reported(tmp_path, monkeypatch):
    sent = []

    async def fake_text(token, chat, text, buttons=None):
        sent.append(("text", text, buttons))
        return {"message_id": 1}

    async def fake_photo(token, chat, png, caption, buttons=None):
        sent.append(("photo", caption, buttons))
        return {"message_id": 2}

    monkeypatch.setattr(notify, "telegram", fake_text)
    monkeypatch.setattr(notify, "telegram_photo", fake_photo)
    s = Settings(db_path=str(tmp_path / "t.db"), telegram_token="t", telegram_chat_id="1")
    store = Store(s.db_path)
    hl = FakeHL()
    tr = tracking.GroupTracker(s, store, FakeReflex(), hl)
    snaps = {"reflex": {"convergences": [{"coin": "STRK", "side": "short", "n": 2, "span_h": 0.3, "ts": 2000.0,
                                          "key": "conv:STRK:short:1000:2"}], "coins": {}}}

    out = asyncio.run(tr.step(snaps, {}, set(), now=2100.0))
    assert out["groups_created"] == 1 and out["groups_announced"] == 1
    kind, caption, buttons = sent[0]
    assert kind == "photo" and caption.startswith("#1 STRK short · 2 profitable traders in 0.3h")
    assert buttons["inline_keyboard"][0][0]["callback_data"] == "f:1"
    assert store.picks(origin="group")[0]["entry_price"] == 0.07

    # same convergence again: no new group, no second announcement
    out = asyncio.run(tr.step(snaps, {}, set(), now=2400.0))
    assert out["groups_created"] == 0 and len(sent) == 1

    # not followed: a close is recorded but not pushed
    hl.closed.add("0xa1")
    asyncio.run(tr.step(snaps, {}, set(), now=3000.0))
    assert len(sent) == 1
    m = {x["address"]: x for x in store.members(1)}
    assert m["0xa1"]["status"] == "closed" and round(m["0xa1"]["pnl_pct"], 3) == 0.087

    # followed: the last close is pushed, then the group summary
    tr.set_follow(1, True)
    hl.closed.add("0xb2")
    asyncio.run(tr.step(snaps, {}, set(), now=4000.0))
    texts = [t for k, t, _ in sent[1:]]
    assert texts[0].startswith("#1 STRK short: 0xb2 closed @ 0.065") and "0 of 2 still in" in texts[0]
    assert texts[1].startswith("#1 STRK short: all out. 2 of 2 closed in profit")
    assert store.get_group(1)["status"] == "closed"


def test_seeding_and_stale_groups_are_recorded_not_announced(tmp_path, monkeypatch):
    calls = []

    async def fake(*a, **k):
        calls.append(a)

    monkeypatch.setattr(notify, "telegram", fake)
    monkeypatch.setattr(notify, "telegram_photo", fake)
    s = Settings(db_path=str(tmp_path / "t.db"))
    tr = tracking.GroupTracker(s, Store(s.db_path), FakeReflex(), FakeHL())
    snaps = {"reflex": {"convergences": [{"coin": "STRK", "side": "short", "n": 2, "span_h": 0.3, "ts": 2000.0,
                                          "key": "conv:STRK:short:1000:2"}], "coins": {}}}
    out = asyncio.run(tr.step(snaps, {}, {"reflex"}, now=2100.0))
    assert out["groups_created"] == 1 and out["groups_announced"] == 0 and not calls
