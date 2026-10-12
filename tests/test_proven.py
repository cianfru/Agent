import asyncio
import json

from synth import proven as P
from synth.position_traders import PositionTraders, load_owners
from synth.config import Settings
from synth.store import Store

DAY_MS = P.DAY_MS
NOW = 1_791_000_000.0
NOW_MS = int(NOW * 1000)
W = "0x" + "1" * 40


_tid = iter(range(10 ** 9))


def fill(coin, t, px, sz, side, start):
    return {"coin": coin, "time": t, "px": str(px), "sz": str(sz), "side": side, "startPosition": str(start),
            "tid": next(_tid)}


def round_trip(coin, t0, days, px0, px1, sz=10.0, side="long"):
    """Open at px0, close `days` later at px1."""
    b, s = ("B", "A") if side == "long" else ("A", "B")
    sgn = 1 if side == "long" else -1
    return [fill(coin, t0, px0, sz, b, 0), fill(coin, t0 + int(days * DAY_MS), px1, sz, s, sgn * sz)]


def history():
    """Six winning swing positions, one losing one, one scalp: proven (PF well over 1.5)."""
    out = []
    for i in range(6):
        out += round_trip(f"C{i}", NOW_MS - (200 - 20 * i) * DAY_MS, 5, 100, 110)
    out += round_trip("L", NOW_MS - 50 * DAY_MS, 4, 100, 95)
    out += round_trip("S", NOW_MS - 40 * DAY_MS, 0.02, 100, 101)
    return out


class FakeHL:
    """Serves Hyperliquid info bodies from a wallet's fills, in pages."""

    def __init__(self, fills, positions=None, subs=(), page=P.PAGE):
        self.fills, self.positions, self.subs, self.page, self.calls = fills, positions or {}, list(subs), page, []

    async def _post(self, body):
        self.calls.append(body["type"])
        if body["type"] == "clearinghouseState":
            return {"assetPositions": [{"position": {"coin": c, "szi": str(p["szi"]), "entryPx": "100",
                                                     "positionValue": str(p["value"]), "unrealizedPnl": str(p["upnl"]),
                                                     "returnOnEquity": str(p.get("roe", 0)), "leverage": {"value": p.get("lev", 1)}}}
                                       for c, p in self.positions.items()], "marginSummary": {"accountValue": "1000000"}}
        if body["type"] == "subAccounts":
            return [{"subAccountUser": s} for s in self.subs]
        end = body.get("endTime", 10 ** 15)
        sel = sorted((f for f in self.fills if body["startTime"] <= f["time"] <= end), key=lambda f: f["time"])
        if body["type"] == "userTwapSliceFillsByTime":
            return []
        return sel[:self.page]


def refresher(tmp_path, seed=None):
    store = Store(str(tmp_path / "t.db"))
    r = P.Refresher(store, seed or {}, NOW - 86400)
    r.weight_per_min = 10 ** 9                       # no pacing in tests
    return r, store


def test_roster_rule_on_the_leaderboard():
    def row(a, av, pnl, roi, vlm, allt):
        return {"ethAddress": a, "accountValue": str(av), "windowPerformances": [
            ["month", {"pnl": str(pnl), "roi": str(roi), "vlm": str(vlm)}], ["allTime", {"pnl": str(allt)}]]}
    rows = [row("0xA", 50_000, 20_000, 0.6, 1e6, 30_000),        # in
            row("0xB", 50_000, 20_000, 0.9, 1e8, 30_000),        # market maker
            row("0xC", 50_000, 20_000, 40.0, 1e6, 30_000),       # began the month nearly empty
            row("0xD", 50_000, 20_000, 0.6, 1e6, 10_000),        # all-time under this month
            row("0xE", 50_000, 5_000, 0.6, 1e6, 30_000),         # under $10K profit
            row("0xF", 50_000, 30_000, 0.8, 1e6, 40_000)]        # in, higher ROI
    assert list(P.roster_candidates(rows)) == ["0xf", "0xa"]


def test_fold_by_average_cost_and_across_calls():
    fs = P.compact([fill("X", 1, 100, 10, "B", 0), fill("X", 2, 120, 10, "B", 10), fill("X", 3, 130, 5, "A", 20),
                    fill("X", 4, 90, 30, "A", 15)])                        # sells 15 to flat, then 15 short
    one = {}
    closed = P.fold(one, fs)
    assert closed == [["X", "long", 1, 4, 2600.0, -200.0]]                # avg 110: +20 on 5, -20 on 15
    assert one["X"]["seg"]["side"] == "short" and one["X"]["pos"] == -15.0
    two = {}
    assert P.fold(two, fs[:2]) + P.fold(two, fs[2:]) == closed and two == one


def test_end_check_drops_a_coin_that_disagrees_with_live():
    coins = {}
    closed = P.fold(coins, P.compact([fill("X", 1, 100, 10, "B", 0), fill("X", 2, 110, 10, "A", 10),
                                      fill("Y", 1, 100, 10, "B", 0)]))
    kept = P.reconcile(coins, closed, live={})                            # Y is flat now: liquidated, say
    assert kept == closed and coins["Y"]["seg"] is None
    kept = P.reconcile(coins, closed, live={"X": {"side": "short"}})
    assert kept == []


def test_bots_and_moves():
    fast = P.compact([fill("X", i * 600_000, 100, 1, "B" if i % 2 else "A", 0) for i in range(200)])
    assert P.bot_like(fast, 0, 86_400_000)
    assert not P.bot_like(P.compact(history()), NOW_MS - 14 * DAY_MS, NOW_MS)
    last = {}
    moves = P.add_moves([], last, P.compact([fill("X", 0, 1, 1, "B", 0), fill("X", 300_000, 1, 1, "B", 1),
                                              fill("X", 2_000_000, 1, 1, "B", 2)]), 0)
    assert moves == [["X", "B", 0], ["X", "B", 2_000_000]]


def test_evaluate_counts_open_positions_held_three_days():
    coins = {}
    closed = P.fold(coins, P.compact(history()))
    summary, entry = P.evaluate(W, closed, coins, {}, NOW_MS, {"alltime_pnl": 5000})
    assert summary["proven"] and entry["swing_positions"] == 7 and entry["win_rate"] == round(6 / 7, 3)
    assert entry["swing_pnl"] == 6 * 100 - 50 and entry["profit_factor"] == 12.0 and entry["alltime_pnl"] == 5000
    # four winners only: not proven; an open position held 10 days at +$300 makes the fifth
    few = [p for p in closed if p[0] in ("C0", "C1", "C2", "C3")]
    assert P.evaluate(W, few, coins, {}, NOW_MS)[1] is None
    coins2 = {}
    P.fold(coins2, P.compact([fill("O", NOW_MS - 10 * DAY_MS, 100, 10, "B", 0)]))
    s2, e2 = P.evaluate(W, few, coins2, {"O": {"side": "long", "notional": 1100, "upnl": 300}}, NOW_MS)
    assert e2 and e2["swing_positions"] == 5 and e2["open_swing_pnl"] == 300 and e2["open_since"]["O:long"]


def test_same_minutes_links():
    a = [["X", "B", i * 3_600_000] for i in range(6)]
    b = [["X", "B", i * 3_600_000 + 60_000] for i in range(6)]
    c = [["X", "B", i * 3_600_000 + 1_800_000] for i in range(6)]
    assert P.same_minutes_pairs({"a": a, "b": b, "c": c}) == [["a", "b"]]


def test_owner_map_joins_file_groups_and_new_pairs(tmp_path):
    f = tmp_path / "owners.json"
    f.write_text(json.dumps({"groups": [["0xa", "0xb"]]}))
    o = load_owners(f, pairs=[["0xb", "0xc"], ["0xd", "0xe"]])
    assert o["0xc"] == o["0xa"] == "0xa" and o["0xe"] == "0xd" and "0xz" not in o


def test_first_check_lists_a_proven_trader_then_reads_only_what_is_new(tmp_path):
    r, store = refresher(tmp_path)
    store.pt_cand_pool({W: (["roster"], {"av": 900_000, "alltime_pnl": 7000})}, NOW)
    hl = FakeHL(history(), subs=["0x" + "2" * 40])
    assert r.next(NOW) == W
    assert asyncio.run(r.check(hl, W, NOW)) == "proven"
    listed = store.pt_list()[W]
    assert listed["tier"] == "proven" and listed["swing_positions"] == 7 and listed["alltime_pnl"] == 7000
    assert r.owner_pairs() == [[W, "0x" + "2" * 40]]                  # $500K+ account: sub-accounts read
    row = store.pt_cand_get(W)
    assert row["status"] == "proven" and len(row["closed"]) == 8 and row["moves"]
    # two days later: only fills after the last one read; a new losing swing position is added
    hl.fills += round_trip("N", NOW_MS + 1 * DAY_MS, 3, 100, 80)
    hl.calls.clear()
    later = NOW + 5 * 86400
    assert r._due(later)[0] == W
    asyncio.run(r.check(hl, W, later))
    assert store.pt_list()[W]["swing_positions"] == 8 and "subAccounts" not in hl.calls
    assert len(store.pt_cand_get(W)["closed"]) == 9


def test_too_active_and_bots_are_left_out(tmp_path, monkeypatch):
    r, store = refresher(tmp_path)
    store.pt_cand_pool({W: (["big"], {"av": 1e6})}, NOW)
    monkeypatch.setattr(P, "PAGE", 1)
    monkeypatch.setattr(P, "PROBE_PAGES", 2)
    busy = FakeHL([fill("X", NOW_MS - i * 60_000, 100, 1, "B", i) for i in range(5)], page=1)   # 5 fills, 2 pages max
    assert asyncio.run(r.check(busy, W, NOW)) == "busy"
    monkeypatch.setattr(P, "PAGE", 2000)
    bot = FakeHL([fill("X", NOW_MS - i * 600_000, 100, 1, "B" if i % 2 else "A", 0) for i in range(500)])
    assert asyncio.run(r.check(bot, W, NOW)) == "bot" and W not in store.pt_list()


def test_seed_traders_keep_their_place_for_30_days_then_drop(tmp_path, monkeypatch):
    seed_trader = {"address": W, "tier": "proven", "swing_positions": 9, "swing_pnl": 1, "win_rate": 1,
                   "median_hold_days": 5, "median_size": 1}
    s = Settings(db_path=str(tmp_path / "t.db"))
    store = Store(s.db_path)
    pt = PositionTraders(s, store, seed={"as_of": "2026-10-10", "traders": [seed_trader]}, hl=FakeHL([]))
    pt.refresh.weight_per_min = 10 ** 9
    assert W in pt.traders                                            # not read yet: the seed counts
    asyncio.run(pt.refresh.check(FakeHL([]), W, NOW))                # no record now: not proven
    assert pt.traders[W]["swing_positions"] == 9                      # kept, grace from the seed's date
    gone = store.pt_list_prune({"proven": pt.refresh.seed_ts + 30 * 86400 + 1})
    assert gone == [W] and W not in pt.traders                        # dropped, and the seed does not bring it back


def test_pages_join_on_the_boundary_millisecond(tmp_path, monkeypatch):
    r, store = refresher(tmp_path)
    monkeypatch.setattr(P, "PAGE", 2)
    fs = [fill("X", 1, 100, 1, "B", 0), fill("X", 2, 100, 1, "B", 1), fill("X", 2, 101, 1, "B", 2),
          fill("X", 3, 102, 1, "B", 3)]
    got, ok = asyncio.run(r._fills(FakeHL(fs, page=2), W, 0, 10))
    assert ok and [f[1] for f in got] == [1, 2, 2, 3]                 # the second fill at t=2 is not lost


def test_long_held_winner_rule():
    coins = {}
    P.fold(coins, P.compact([fill("W", NOW_MS - 30 * DAY_MS, 100, 1000, "B", 0)]))
    live = {"W": {"side": "long", "size": 1000.0, "entry_px": 100.0, "notional": 150_000, "upnl": 50_000, "roe": 1.5, "lev": 3.0}}
    sm = P.live_summary(live, coins, 200_000, NOW)
    w = sm["positions"]["W"]
    assert w["move"] == 0.5 and w["roe"] == 1.5 and w["since"] == (NOW_MS - 30 * DAY_MS) / 1000
    r = P.rides_long_winner(sm, {"alltime_pnl": 40_000}, NOW)
    assert r["tier"] == "riding" and r["best"]["move"] == 0.5 and r["best"]["opened"] == P._day(NOW_MS - 30 * DAY_MS)
    assert P.rides_long_winner(sm, {"alltime_pnl": -5}, NOW) is None                     # lost money all-time
    assert P.rides_long_winner(P.live_summary(live, {}, 200_000, NOW), {}, NOW) is None  # open since before the reads
    small = {"W": {**live["W"], "notional": 105_000, "upnl": 5_000}}
    assert P.rides_long_winner(P.live_summary(small, coins, 200_000, NOW), {}, NOW) is None
    recent = {}
    P.fold(recent, P.compact([fill("W", NOW_MS - 5 * DAY_MS, 100, 1000, "B", 0)]))
    assert P.rides_long_winner(P.live_summary(live, recent, 200_000, NOW), {}, NOW) is None   # 5 days is not long


def test_check_lists_any_account_riding_a_long_held_winner(tmp_path):
    r, store = refresher(tmp_path)
    store.pt_cand_pool({W: (["roster"], {"av": 60_000, "alltime_pnl": 40_000})}, NOW)
    hl = FakeHL([fill("W", NOW_MS - 30 * DAY_MS, 100, 1000, "B", 0)],
                positions={"W": {"szi": 1000, "value": 150_000, "upnl": 50_000, "roe": 1.5, "lev": 3}})
    assert asyncio.run(r.check(hl, W, NOW)) == "riding"
    e = store.pt_list()[W]
    assert e["tier"] == "riding" and e["rule"] == "long-held winner" and e["best"]["coin"] == "W"
    live = store.pt_cand_live()[W]
    assert live["equity"] == 1_000_000 and live["positions"]["W"]["move"] == 0.5 and live["positions"]["W"]["roe"] == 1.5


def test_margin_alone_can_make_a_long_held_winner():
    coins = {}
    P.fold(coins, P.compact([fill("W", NOW_MS - 20 * DAY_MS, 100, 1000, "B", 0)]))
    live = {"W": {"side": "long", "size": 1000.0, "entry_px": 100.0, "notional": 115_000, "upnl": 15_000, "roe": 6.74}}
    r = P.rides_long_winner(P.live_summary(live, coins, 50_000, NOW), {"alltime_pnl": 1}, NOW)
    assert r and r["best"]["move"] == 0.15 and r["best"]["roe"] == 6.74     # +15% on price, +674% on margin
    low = {"W": {**live["W"], "roe": 1.5}}
    assert P.rides_long_winner(P.live_summary(low, coins, 50_000, NOW), {}, NOW) is None
