import asyncio
import json
import time

from synth import analyst, chat, notify, service
from synth.config import Settings
from synth.position_traders import PositionTraders
from synth.store import Store
from tests.test_chat import FakeClient, response, text_block, tool_block

TRADERS = ["0x" + c * 40 for c in "abcdef"]
OWNER = "0x" + "9" * 40
RECORD = {"tier": "proven", "swing_positions": 12, "swing_pnl": 900_000, "realized_swing_pnl": 700_000,
          "open_swing_pnl": 200_000, "profit_factor": 2.4, "win_rate": 0.6, "median_hold_days": 9, "median_size": 150_000}
READ = {"send": True, "headline": "Tracked traders are shorting SUI into the bounce",
        "read": "Four proven traders added SUI shorts in the last day while the crowd bought.",
        "market_lean": "neutral",
        "positions": [{"coin": "sui", "side": "long", "take": "trim", "why": "Four proven traders are short against you."}],
        "views": [{"coin": "SUI", "lean": "short", "strength": 2, "why": "Proven traders adding, engine stretched.",
                   "changes_if": "they cut those shorts"}]}


def make(tmp_path, monkeypatch, responses, brief_hour=24, **kw):
    sent = []

    async def fake(token, chat_id, text, buttons=None):
        sent.append(text)
        return {"message_id": 1}

    monkeypatch.setattr(notify, "telegram", fake)
    s = Settings(db_path=str(tmp_path / "t.db"), telegram_token="t", telegram_chat_id="1", brief_hour_utc=brief_hour)
    store = Store(s.db_path)
    agent = service.Agent(s, store)
    agent.positions = PositionTraders(s, store, seed={"as_of": "2026-10-10", "traders": []})
    agent.positions.enabled = False
    agent.tracker.last_mids = {"SUI": 1.8, "BTC": 98_000.0}
    client = FakeClient(responses)
    agent.chat = chat.Chat(agent, "claude-opus-5-5", client=client)
    agent.analyst = analyst.Analyst(agent, "claude-opus-5-5", client=client, quiet="", **kw)
    return agent, sent


def seed(store, now):
    store.add_series("reflex", {"c": {"BTC": [30, 3e7, 20, 2e7, 100_000.0], "SUI": [10, 1e6, 5, 5e5, 2.0]}}, ts=now - 86400)
    store.add_series("reflex", {"c": {"BTC": [25, 2.5e7, 25, 2.5e7, 98_000.0], "SUI": [12, 1.2e6, 4, 4e5, 1.8]}}, ts=now - 60)
    store.put_snapshot("reflex", {"positions": {"BTC": [25, 2.5e7, 25, 2.5e7, 98_000.0], "SUI": [12, 1.2e6, 4, 4e5, 1.8]},
                                  "engine": {"SUI": {"1d": "TRIM", "4h": "WAIT"}}})
    for a in TRADERS[:4]:
        store.pt_list_upsert(a, {**RECORD, "address": a}, now)
        store.pt_open_position(a, "SUI", "short", now - 3 * 86400, 1, now, 200_000, 2.1, 30_000, 0, None)
        store.pt_add_flow(now - 3600, a, "SUI", "short", 150_000, 1.9)
    store.put_snapshot("owner_positions", {"ts": now, "wallets": {OWNER: {"equity": 50_000, "positions": {
        "SUI": {"side": "long", "size": 11_000, "notional": 20_000, "upnl": -1_500, "entry_px": 1.95, "lev": 3.0,
                "roe": -0.2, "liq": 1.2}}}}})


def test_scan_puts_his_position_beside_what_the_groups_do(tmp_path, monkeypatch):
    agent, _ = make(tmp_path, monkeypatch, [])
    now = time.time()
    seed(agent.store, now)
    s = analyst.scan(agent, now)
    sui = next(r for r in s["coins"] if r["coin"] == "SUI")
    assert sui["tracked"]["short"] == 4 and sui["tracked"]["lean"] == "short"
    assert sui["tracked_24h"]["short"]["adders"] == 4 and sui["crowd"]["lean"] == "long"
    assert sui["engine"] == {"daily": "stretched", "4h": "no clear setup"} and sui["change_24h_pct"] == -10.0
    btc = s["direction"][0]
    assert btc["coin"] == "BTC" and btc["change_24h_pct"] == -2.0
    mine = s["my_positions"][0]
    assert (mine["coin"], mine["side"], mine["value"]) == ("SUI", "long", 20_000)
    # the crowd leans his way, but it is not counted (S2); tracked traders are
    assert mine["against"] == ["tracked traders lean short (4 short vs 0 long)", "4 tracked traders added shorts in 24h"]


def test_changes_weigh_what_moved_and_mark_his_positions():
    assert analyst.changes(None, {}) == [{"w": analyst.TRIGGER, "text": "first read", "owner": False}]
    prev = {"market": {"crowd_long_pct": 60.0}, "direction": [{"coin": "BTC", "price": 100_000.0, "groups_agree": "mixed"}],
            "coins": [{"coin": "SUI", "price": 2.0, "tracked": {"lean": "split"}}],
            "my_positions": [{"source": "wallet 0x9999", "coin": "SUI", "side": "long", "move_pct": 2.6, "against": []},
                             {"source": "watch list", "coin": "HYPE", "side": None, "price": 40.0, "against": []}],
            "alerts_24h": ["old"]}
    cur = {"market": {"crowd_long_pct": 48.0}, "direction": [{"coin": "BTC", "price": 96_000.0, "groups_agree": "all short"}],
           "coins": [{"coin": "SUI", "price": 1.7, "tracked": {"lean": "short"},
                      "tracked_24h": {"short": {"adders": 3, "cutters": 0, "net_usd": 400_000}}}],
           "my_positions": [{"source": "wallet 0x9999", "coin": "SUI", "side": "long", "move_pct": -12.8,
                             "against": ["tracked traders lean short (4 short vs 0 long)"]},
                            {"source": "watch list", "coin": "HYPE", "side": None, "price": 44.0, "against": []},
                            {"source": "watch list", "coin": "ENA", "side": "long", "move_pct": 0.0, "against": []}],
           "alerts_24h": ["old", "new one"]}
    got = {c["text"]: (c["w"], c["owner"]) for c in analyst.changes(prev, cur)}
    assert got["profitable traders -12 points long (60% to 48%)"] == (3, False)
    assert got["BTC -4.0% since the last read"] == (2, False)
    assert got["BTC: the groups now read all short (were mixed)"] == (2, False)
    assert got["SUI: tracked now lean short (were split)"] == (2, False)
    assert got["SUI: 3 tracked traders added shorts in 24h"] == (2, False)
    assert got["SUI -15% since the last read"] == (1, False)
    assert got["1 new alerts: new one"] == (1, False)
    assert got["your SUI long: tracked traders lean short (4 short vs 0 long)"] == (4, True)
    assert got["your SUI long moved -15 points since the last read (-13% from entry)"] == (2, True)
    assert got["HYPE (watched) +10% since the last read"] == (2, True)
    assert got["you are now watching ENA long"] == (1, True)


def test_a_read_goes_out_with_his_positions_and_views_then_waits_for_change(tmp_path, monkeypatch):
    agent, sent = make(tmp_path, monkeypatch, [response([text_block(json.dumps(READ))])])
    now = time.time()
    seed(agent.store, now)
    out = asyncio.run(agent.analyst.step(now))
    assert out["reason"] == "market changed" and out["sent"] and len(sent) == 1
    text = sent[0]
    assert text.splitlines()[1] == READ["headline"]
    assert "Yours\nSUI long · trim: Four proven traders are short against you." in text
    assert "Views\nSUI short · firm: Proven traders adding, engine stretched. Off if they cut those shorts." in text
    assert "Record" not in text                                       # the record rides on the morning read only
    first, = agent.analyst.client.calls                                # one call, no tools: the scan holds it all
    assert first["output_config"]["effort"] == "medium" and first["output_config"]["format"]["type"] == "json_schema"
    assert first["fallbacks"] == "default" and "tools" not in first
    task = first["messages"][0]["content"]
    assert "- first read" in task and '"my_positions"' in task and "tracked traders lean short" in task
    pick = agent.store.picks(origin="read")[0]
    assert (pick["coin"], pick["direction"], pick["conviction"], pick["entry_price"]) == ("SUI", "short", 2, 1.8)
    assert abs(agent.analyst.spent_today(now) - 0.05) < 1e-9

    # nothing changed: no read, no model call
    assert asyncio.run(agent.analyst.step(now + 600)) is None and len(agent.analyst.client.calls) == 1

    # big money turns against his long: a new reason against him, but within 30 minutes of the last read it waits
    agent.store.add_brief("pt_bigmoney", {"coins": {"SUI": {"long_n": 1, "short_n": 5, "long_usd": 1e5, "short_usd": 9e5}}})
    assert asyncio.run(agent.analyst.step(now + 900)) is None
    agent.analyst.client.responses = [response([text_block(json.dumps({**READ, "views": READ["views"] * 2}))])]
    out = asyncio.run(agent.analyst.step(now + 1900))
    assert out["reason"] == "your position" and out["sent"]
    assert "your SUI long: big money leans short" in agent.store.latest_brief("read")["changes"]
    assert len(agent.store.picks(origin="read")) == 1                # the same view within a day is not logged again


def test_held_back_when_the_model_sees_nothing_worth_saying(tmp_path, monkeypatch):
    agent, sent = make(tmp_path, monkeypatch, [response([text_block(json.dumps({**READ, "send": False}))])])
    now = time.time()
    seed(agent.store, now)
    out = asyncio.run(agent.analyst.step(now))
    assert not out["sent"] and not sent and not agent.store.picks(origin="read")
    b = agent.store.latest_brief("read")
    assert b["sent"] is False and b["changes"] == ["first read"] and agent.store.get_snapshot("read_scan")


def test_budget_and_quiet_hours_hold_reads_but_never_the_morning_read(tmp_path, monkeypatch):
    agent, sent = make(tmp_path, monkeypatch, [response([text_block(json.dumps({**READ, "send": False}))])],
                       brief_hour=0, daily_usd=0.01)
    now = time.time()
    seed(agent.store, now)
    out = asyncio.run(agent.analyst.step(now))
    assert out["reason"] == "morning" and out["sent"] and len(sent) == 1      # the morning read always goes out
    agent.store.put_snapshot("read_scan", {})                                  # make everything look new
    assert asyncio.run(agent.analyst.step(now + 3 * 3600)) is None             # today's budget is spent
    assert analyst.quiet_hours("22-6") == {22, 23, 0, 1, 2, 3, 4, 5} and analyst.quiet_hours("") == set()


def test_record_counts_views_their_way(tmp_path, monkeypatch):
    agent, _ = make(tmp_path, monkeypatch, [])
    for i, ret in enumerate([0.03, -0.01, 0.02, 0.05, -0.02, 0.01]):
        pid = agent.store.add_pick("read", "SUI", "short", 2 if i < 3 else 1, 1.8, "why", {})
        agent.store.set_pick_return(pid, "3d", ret)
    rec = analyst.record(agent.store)
    a = rec["3d"]["all"]
    assert (a["n"], a["right"], a["median_pct"], a["vs_market"]["n"]) == (6, 4, 1.5, 0)   # no market move yet
    assert rec["3d"]["firm_or_strong"]["n"] == 3 and (rec["shorts"], rec["3d"]["short"]["right"]) == (6, 4)
    assert analyst.record_text(rec) == "Record: 4 of 6 views right after 3 days (67%), median +1.5% their way."


def test_chat_sees_his_positions_and_the_latest_read(tmp_path, monkeypatch):
    agent, _ = make(tmp_path, monkeypatch, [response([text_block(json.dumps(READ))])])
    now = time.time()
    seed(agent.store, now)
    asyncio.run(agent.analyst.step(now))
    mine = json.loads(agent.chat.execute("my_positions", {}, now))
    assert mine["wallets_connected"] and mine["positions"][0]["against"][0].startswith("tracked traders lean short")
    last = json.loads(agent.chat.execute("latest_read", {}, now))
    assert last["reads"][-1]["headline"] == READ["headline"] and last["record"]["logged"] == 1


def test_watch_list_is_followed_like_a_wallet_position(tmp_path, monkeypatch):
    agent, sent = make(tmp_path, monkeypatch, [])
    now = time.time()
    seed(agent.store, now)
    agent.store.put_snapshot("owner_positions", {"ts": now, "wallets": {}})
    assert analyst.parse_watch(["$sui", "LONG", "2.0", "5k"]) == {"coin": "SUI", "side": "long", "entry": 2.0,
                                                                 "size_usd": 5000.0}
    assert analyst.parse_watch(["hype"]) == {"coin": "HYPE", "side": None, "entry": None, "size_usd": None}

    async def fake_tg(token, method, **params):
        return {}

    monkeypatch.setattr(notify, "tg", fake_tg)
    from synth import bot
    asyncio.run(bot.handle(agent, {"message": {"chat": {"id": 1}, "text": "/watch SUI long 2.0 5k"}}))
    assert sent[-1].splitlines() == ["Watching", "SUI long from 2.00 · now 1.80 (-10.0%) · tracked 0 long / 4 short, "
                                     "4 added shorts in 24h",
                                     "  against you: tracked traders lean short (4 short vs 0 long); "
                                     "4 tracked traders added shorts in 24h"]
    m = analyst.scan(agent, now)["my_positions"][0]
    assert (m["source"], m["move_pct"], m["upnl"]) == ("watch list", -10.0, -500)
    # in plain words through the chat, and off again
    json.loads(agent.chat.execute("watch_add", {"coin": "hype"}, now))
    assert [w["coin"] for w in agent.store.watch_list()] == ["SUI", "HYPE"]
    asyncio.run(bot.handle(agent, {"message": {"chat": {"id": 1}, "text": "/unwatch sui"}}))
    assert sent[-1] == "Took SUI off the watch list." and [w["coin"] for w in agent.store.watch_list()] == ["HYPE"]


class FakeReflex:
    """Reflex's scan rows: SUI and SPX are scanned, anything else is not (404)."""
    ROWS = {"SUI": {"1d": {"signal": "TRIM", "regime": "MARKUP", "heat": 82, "zscore": 1.71234, "sector": "L1",
                           "exhaustion_state": "NEUTRAL", "conditions_detail": ["long"]},
                    "4h": {"signal": "WAIT", "regime": "REACC"}},
            "SPX": {"1d": {"signal": "LIGHT_LONG", "regime": "ACCUM", "heat": 30, "signal_reason": "base forming"},
                    "4h": {"signal": "STRONG_LONG", "regime": "MARKUP"}}}

    def __init__(self):
        self.paths = []

    async def get(self, path):
        import httpx
        self.paths.append(path)
        coin = path.split("symbol=")[1].split("&")[0]
        tf = path.split("timeframe=")[1]
        if coin not in self.ROWS:
            req = httpx.Request("GET", "http://x" + path)
            raise httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))
        return {"as_of": 1, "row": self.ROWS[coin][tf]}


def test_engine_rows_for_any_scanned_coin():
    from synth.sources import reflex
    rows = asyncio.run(reflex.engine_rows(FakeReflex(), "spx"))
    assert rows["1d"] == {"signal": "LIGHT_LONG", "regime": "ACCUM", "heat": 30, "signal_reason": "base forming"}
    assert asyncio.run(reflex.engine_rows(FakeReflex(), "NOPE")) is None
    assert analyst.engine_words(rows) == {"daily": "leaning up", "4h": "strong up-setup", "regime": "building a base",
                                          "regime_4h": "trending up", "heat": 30, "why_daily": "base forming"}


def test_chat_reads_the_engine_for_a_coin_outside_the_feed(tmp_path, monkeypatch):
    agent, _ = make(tmp_path, monkeypatch, [response([tool_block("coin", {"coin": "SPX"})], stop="tool_use"),
                                            response([text_block("SPX: base forming on the daily.")])])
    agent.reflex = FakeReflex()
    assert asyncio.run(agent.chat.reply("1", "what does the engine say on SPX?")) == "SPX: base forming on the daily."
    result = json.loads(agent.chat.client.calls[1]["messages"][-1]["content"][0]["content"])
    assert result["engine_now"]["daily"] == "leaning up" and result["engine_now"]["regime"] == "building a base"


def test_a_read_fills_the_engine_for_coins_the_feed_leaves_short(tmp_path, monkeypatch):
    agent, _ = make(tmp_path, monkeypatch, [response([text_block(json.dumps(READ))])])
    now = time.time()
    seed(agent.store, now)
    agent.reflex = FakeReflex()
    asyncio.run(agent.analyst.step(now))
    task = agent.analyst.client.calls[0]["messages"][0]["content"]
    sui = next(r for r in json.loads(task.split("<scan>\n")[1].split("\n</scan>")[0])["coins"] if r["coin"] == "SUI")
    assert sui["engine"]["regime"] == "trending up" and sui["engine"]["heat"] == 82 and sui["engine"]["zscore"] == 1.71
    assert any("symbol=BTC" in p for p in agent.reflex.paths)            # BTC had no reading either: asked, 404, fine
