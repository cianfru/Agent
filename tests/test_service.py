import asyncio
import copy

from synth import service
from synth.config import Settings
from synth.llm import Alert
from synth.store import Store

BASE = {
    "source": "cowboy",
    "market": {"consensus_1d": "RISK-ON", "consensus_1d_strength": 60, "fear_greed": 55},
    "coins": {"HYPE": {"price": 40.0, "signal_1d": "WAIT", "wallets_30d": {"state": "up"}}},
    "smart_cohorts": {},
    "wallets": {"HYPE": [
        {"address": "0xa", "rank": 2, "cohort": "Money Printer", "smart": True, "net_delta_usd": 0},
        {"address": "0xb", "rank": 9, "cohort": "Smart Money", "smart": True, "net_delta_usd": 0},
    ]},
}


class FakeSynth:
    def __init__(self):
        self.calls = 0

    def alert(self, event, context):
        self.calls += 1
        return Alert(headline=event["title"][:80], coin=event["coin"], direction="long", conviction=4,
                     why="x", against=None), {"input_tokens": 1}


class FakeHL:
    async def mids(self):
        return {}

    async def positions(self, address):
        return {}

    async def closing_fills(self, *a):
        return []

    async def candles(self, *a):
        return []


def offline(agent):
    agent.tracker._hl = FakeHL()
    agent.positions.enabled = False   # no network: the tracked traders have their own tests
    return agent


def make(tmp_path, snaps):
    s = Settings(db_path=str(tmp_path / "t.db"), alert_min_score=60, llm_alerts=True)
    agent = offline(service.Agent(s, Store(s.db_path), FakeSynth()))
    it = iter(snaps)

    async def collect():
        return {"cowboy": next(it)}

    agent.collect = collect
    return agent


def test_first_cycle_seeds_then_convergence_alerts_once(tmp_path):
    moved = copy.deepcopy(BASE)
    for h in moved["wallets"]["HYPE"]:
        h["net_delta_usd"] = 500_000
    agent = make(tmp_path, [BASE, moved, moved])

    first = asyncio.run(agent.cycle())
    assert first["alerts"] == 0 and first["sources"]["cowboy"]["seeded"]

    second = asyncio.run(agent.cycle())
    kinds = {e["kind"] for e in second["events"]}
    assert "smart_convergence" in kinds  # logged; groups announce convergences
    assert second["alerts"] >= 1         # the two big wallet moves ping
    assert agent.synth.calls == second["alerts"]
    # conviction 4 long alert is journalled with the entry price
    picks = agent.store.picks()
    assert picks and picks[0]["coin"] == "HYPE" and picks[0]["entry_price"] == 40.0

    # same state again: nothing new, no repeat alert
    third = asyncio.run(agent.cycle())
    assert third["new_events"] == 0 and third["alerts"] == 0


def test_pick_scoring(tmp_path):
    agent = make(tmp_path, [])
    pid = agent.store.add_pick("brief", "HYPE", "short", 3, 40.0, "t", {})
    agent.store._db.execute("UPDATE picks SET ts=ts-87000 WHERE id=?", (pid,))    # 1d horizon 10 minutes ago
    snaps = {"cowboy": {"coins": {"HYPE": {"price": 36.0}}}}
    assert asyncio.run(agent.score_picks(snaps)) == 1
    p = agent.store.picks()[0]
    assert p["ret_1d"] == 0.1 and p["ret_3d"] is None
    assert asyncio.run(agent.score_picks({"cowboy": {"coins": {"HYPE": {"price": 30.0}}}})) == 0   # written once


def test_digest_trims_wallets():
    d = service.build_digest({"cowboy": BASE}, [])
    assert "wallets" not in d["cowboy"]
    assert d["cowboy"]["smart_holders"]["HYPE"][0] == {"rank": 2, "cohort": "Money Printer", "net_delta_usd": 0}


def test_only_smart_money_kinds_alert_and_daily_cap(tmp_path):
    from synth import detectors

    s = Settings(db_path=str(tmp_path / "t.db"), alert_min_score=60, max_alerts_per_day=3)
    agent = offline(service.Agent(s, Store(s.db_path), FakeSynth()))
    agent.store.put_snapshot("cowboy", {"coins": {}})  # not seeding
    batches = iter([
        [{"kind": "signal_change", "coin": "PUMP", "direction": "long", "score": 75, "title": "t", "key": "a",
          "data": {"source": "cowboy"}}],
        [{"kind": "wallet_move", "coin": c, "direction": "long", "score": 90, "title": c, "key": c,
          "data": {"source": "cowboy"}} for c in ("A", "B")],
        [{"kind": "wallet_move", "coin": c, "direction": "long", "score": 90, "title": c, "key": c,
          "data": {"source": "cowboy"}} for c in ("C", "D")],
    ])

    async def collect():
        return {"cowboy": {"coins": {}}}

    agent.collect = collect
    orig = detectors.run_all
    detectors.run_all = lambda *a, **k: next(batches)
    try:
        assert asyncio.run(agent.cycle())["alerts"] == 0       # engine event: logged, no ping
        assert asyncio.run(agent.cycle())["alerts"] == 2       # two smart-money events
        assert asyncio.run(agent.cycle())["alerts"] == 1       # daily cap of 3 reached
    finally:
        detectors.run_all = orig


def test_an_existing_journal_is_migrated_in_place(tmp_path):
    # a database from before the append-only journal: columns are added, posted desk setups keep their post time
    import sqlite3
    from synth.store import Store
    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE picks (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, origin TEXT NOT NULL,
            coin TEXT NOT NULL, direction TEXT NOT NULL, conviction INTEGER, entry_price REAL, thesis TEXT,
            state TEXT NOT NULL, ret_1d REAL, ret_3d REAL, ret_7d REAL);
        CREATE TABLE posts (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL,
            text TEXT NOT NULL, status TEXT NOT NULL, pick_id INTEGER, orbio_post_id TEXT, url TEXT, error TEXT,
            published_ts REAL);
        INSERT INTO picks(ts, origin, coin, direction, entry_price, thesis, state) VALUES (100, 'post', 'HYPE', 'long', 40, 't', '{}');
        INSERT INTO picks(ts, origin, coin, direction, entry_price, thesis, state) VALUES (200, 'post', 'BTC', 'long', 9, 't', '{}');
        INSERT INTO posts(ts, kind, text, status, pick_id, published_ts) VALUES (101, 'daily', 'x', 'published', 1, 150);
        INSERT INTO posts(ts, kind, text, status, pick_id) VALUES (201, 'daily', 'y', 'draft', 2);
    """)
    db.commit()
    db.close()
    store = Store(path)
    got = {p["id"]: p for p in store.picks(limit=10)}
    assert got[1]["published_ts"] == 150 and got[2]["published_ts"] is None
    assert got[1]["void_reason"] is None and got[1]["ret_30d"] is None
    store.void_pick(2, "test")
    assert [p["id"] for p in store.picks(limit=10)] == [1] and len(store.picks(limit=10, include_void=True)) == 2
