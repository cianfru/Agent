"""Operations: one Hyperliquid budget, the status he can ask for, missed market hours filled, backups."""

import asyncio
import os
import time

import httpx

from synth import hyperliquid, market, service
from synth.config import Settings
from synth.store import Store
from tests.test_market import COINS, FakeHL


def test_the_shared_budget_paces_and_a_429_pauses_everyone(monkeypatch):
    b = hyperliquid.Budget(per_min=60)                  # one weight a second
    assert b.reserve(30) == 0.0 and b.reserve(30) == 0.0
    assert 9 <= b.reserve(10) <= 11                     # spent: wait for the bucket to refill
    b.throttled(30)
    assert b.reserve(0) >= 29 and b.stats()["throttled_last_hour"] == 1
    assert hyperliquid.weight({"type": "allMids"}) == 2 and hyperliquid.weight({"type": "candleSnapshot"}) == 20


def test_a_429_is_retried_inside_the_budget(monkeypatch):
    monkeypatch.setattr(hyperliquid.HL, "_post", hyperliquid.HL.__dict__["_post"].__wrapped__
                        if hasattr(hyperliquid.HL.__dict__["_post"], "__wrapped__") else ORIGINAL_POST)
    answers = iter([429, 200])

    def handler(req):
        code = next(answers)
        return httpx.Response(code, json={"BTC": "100"} if code == 200 else {})

    budget = hyperliquid.Budget()
    monkeypatch.setattr(hyperliquid, "BUDGET", budget)
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(hyperliquid.asyncio, "sleep", fake_sleep)
    hl = hyperliquid.HL(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert asyncio.run(hl.mids()) == {"BTC": 100.0}
    assert budget.stats()["throttled_last_hour"] == 1 and any(s >= 29 for s in sleeps)


ORIGINAL_POST = hyperliquid.HL._post


def test_status_and_health_say_what_is_failing(tmp_path):
    s = Settings(db_path=str(tmp_path / "t.db"), reflex_base_url="http://reflex")
    a = service.Agent(s, Store(s.db_path))
    now = time.time()
    a.last_cycle = {"ts": now - 60, "secs": 4.2}
    a.mark("reflex", False, RuntimeError("502 Bad Gateway"), now - 3600)
    a.mark("reflex", False, RuntimeError("502 Bad Gateway"), now - 60)
    a.mark("cowboy", True, now=now - 60)
    ok, problems = a.healthy(now)
    assert not ok and problems[0].startswith("Reflex failing since") and "502" in problems[0]
    st = a.status(now)
    assert st["sources"]["reflex"]["fails"] == 2 and st["sources"]["cowboy"]["ok_ago_min"] == 1
    a.mark("reflex", True, now=now)
    assert a.healthy(now)[0]
    a.last_cycle = {"ts": now - 3 * 3600}
    assert not a.healthy(now)[0]                                     # the poll stalled


def test_one_message_per_outage(tmp_path, monkeypatch):
    from synth import notify
    s = Settings(db_path=str(tmp_path / "t.db"), telegram_token="t", telegram_chat_id="1")
    a = service.Agent(s, Store(s.db_path))
    sent = []

    async def fake(token, chat_id, text, buttons=None, silent=False):
        sent.append(text)
        return {"message_id": 1}

    monkeypatch.setattr(notify, "telegram", fake)
    now = time.time()
    a.mark("reflex", False, "timeout", now - 3 * 3600)
    asyncio.run(a.incidents(now))
    asyncio.run(a.incidents(now + 600))
    assert len(sent) == 1 and sent[0].startswith("Reflex has not answered since")
    a.mark("reflex", True, now=now + 700)
    asyncio.run(a.incidents(now + 800))
    a.mark("reflex", False, "timeout", now + 900)
    asyncio.run(a.incidents(now + 900 + 2 * 3600))
    assert len(sent) == 2                                            # a new outage, a new message


def test_missed_market_hours_are_filled_without_changing_the_kept_ones(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    m = market.MarketIndex(store)
    m.ready = True
    t0 = (time.time() // 3600) * 3600 - 20 * 3600
    store.put_snapshot("index_coins", {"ts": t0, "coins": COINS})
    store.market_add([(t0 - 3600, 100_000.0, 1.0), (t0, 100_000.0, 1.01), (t0 + 10 * 3600, 110_000.0, 1.2)])
    assert m.gap(time.time()) == (t0, t0 + 10 * 3600)
    hl = FakeHL(t0 - 5 * 3600)
    n = asyncio.run(m.fill_gap(hl, time.time(), pace_s=0))
    rows = store.market_rows(t0 - 7200)
    assert n == 9 and len(rows) == 12
    assert rows[1]["basket"] == 1.01 and rows[-1]["basket"] == 1.2       # the readings around it untouched
    levels = [r["basket"] for r in rows[1:]]
    assert levels == sorted(levels)                                     # a steady rise, bent to land on 1.2
    assert m.gap(time.time()) is None                                   # filled once


def test_a_backup_is_a_whole_copy(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.add_pick("read", "SOL", "long", 2, 100.0, "why", {})
    store.backup(str(tmp_path / "copy.db"))
    assert Store(str(tmp_path / "copy.db")).picks(origin="read")[0]["coin"] == "SOL"
    assert os.path.getsize(tmp_path / "copy.db") > 0


def test_a_post_is_claimed_once(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    pid = store.add_post("persona", "text", "draft")
    assert store.claim_post(pid) and not store.claim_post(pid)          # a second tap finds it being sent
    assert store.get_post(pid)["status"] == "posting"


def test_a_failed_backup_leaves_no_file_and_is_tried_again(tmp_path, monkeypatch):
    from synth import api
    s = Settings(db_path=str(tmp_path / "t.db"))
    a = service.Agent(s, Store(s.db_path))
    calls = []

    def broken(path):
        calls.append(path)
        open(path, "wb").close()
        raise OSError("disk full")

    monkeypatch.setattr(a.store, "backup", broken)
    api._daily_backup(a)
    api._daily_backup(a)
    assert len(calls) == 2 and not os.listdir(tmp_path / "backups")    # nothing kept, tried again
    monkeypatch.undo()
    api._daily_backup(a)
    assert [f for f in os.listdir(tmp_path / "backups") if f.endswith(".db")]


def test_a_read_he_asked_for_while_one_ran_follows_unless_that_one_reached_him(tmp_path, monkeypatch):
    import json as _json
    from tests.test_analyst import READ, make, seed
    from tests.test_chat import response, text_block
    agent, sent = make(tmp_path, monkeypatch, [response([text_block(_json.dumps({**READ, "send": False}))]),
                                               response([text_block(_json.dumps(READ))])])
    now = time.time()
    seed(agent.store, now)

    async def go():
        agent.analyst.pending_ask = True                 # he asked while a market read was running
        await agent.analyst.run("market changed", now)
        await asyncio.gather(*agent.analyst._tasks)

    asyncio.run(go())
    reasons = [b["body"]["reason"] for b in agent.store.briefs_since("read", 0)]
    assert reasons == ["market changed", "asked"] and len(sent) == 1   # the held-back read did not reach him
