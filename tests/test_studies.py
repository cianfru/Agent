"""/studies: each study against its declared bar, and when its verdict can come at the pace so far."""

import asyncio
import time

from synth import journal, notify, studies
from synth.config import Settings
from synth.store import Store

DAY = 86400.0


def _pick(store, origin, coin, side, ts, conviction=1, thesis="", state=None, ret=None):
    pid = store.add_pick(origin, coin, side, conviction, 100.0, thesis, state or {})
    store._db.execute("UPDATE picks SET ts=? WHERE id=?", (ts, pid))
    for h, r in (ret or {}).items():
        store.set_pick_return(pid, h, r)
        store.set_pick_market(pid, h, 0.0)                 # the market's move over the same days
    return pid


def test_a_date_is_projected_from_the_pace_so_far():
    now = 1_800_000_000.0
    ten = [now - 5 * DAY + i * DAY / 2 for i in range(10)]               # ten in five days: two a day
    assert studies.project(ten, 60, 3, now, since=now - 5 * DAY) == \
        "about " + studies._date(now + (50 / 2 + 3) * DAY, now)
    assert studies.project(ten[:2], 60, 3, now) == "too early to project"
    done = [now - 10 * DAY + i * 3600 for i in range(60)]                 # the 60th logged 10 days ago
    assert studies.project(done, 60, 3, now) == "due now"
    assert studies.project(done, 60, 30, now).startswith("about ")
    # once a day: each day logged brings one closer, and the last needs three more before it is scored
    days = [now - (4 - i) * DAY for i in range(5)]
    assert studies.project_days(days, 60, 3, now) == "about " + studies._date(now + 58 * DAY, now)
    assert studies.project_days([], 60, 3, now).endswith("(starts with the next daily log)")
    assert studies._date(1_798_761_600, 1_793_750_400) == "1 Jan 2027"     # another year: the year is written


def test_an_empty_record_reads_cleanly(tmp_path):
    rep = studies.report(Store(str(tmp_path / "t.db")))
    txt = studies.text(rep)
    assert "Collecting" in txt and "Done" in txt and "S2 · does the traders' lean" in txt
    assert txt.count("too early to project") >= 3 and "starts with the next daily log" in txt
    assert "S6 · the screen against Reflex's priority (longs): 0 days of 60" in txt


def test_live_counts_come_from_the_logged_picks(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    now = journal.S5_SETUPS_FROM + 10 * DAY
    for i in range(20):                                    # 20 setups in 10 days, one coin each: two a day
        _pick(store, "read", f"C{i}", "long", journal.S5_SETUPS_FROM + i * DAY / 2, ret={"3d": 0.01})
    _pick(store, "read", "C0", "long", journal.S5_SETUPS_FROM + 0.1 * DAY)   # the same bet again: not counted twice
    v = studies.s5(store, now)
    assert v["logged"] == 20 and v["when"] == "about " + studies._date(now + (40 / 2 + 3) * DAY, now)
    # S3's live check: conviction bets with their trader, scored at 30 days
    for i in range(12):
        _pick(store, "position", "SOL", "long", now - (40 - i) * DAY, conviction=2, thesis=f"pt 0xw{i % 4} SOL long",
              ret={"30d": 0.05 if i % 3 else -0.02})
    _pick(store, "position", "ETH", "long", now - DAY, conviction=2, state={"wallet": "0xnew"})
    _pick(store, "position", "BTC", "long", now - DAY, conviction=2, state={"upgraded_late": True})   # apart
    c = studies.s3_forward(store, now)
    assert (c["logged"], c["scored"], c["traders"], c["traders_logged"]) == (13, 12, 4, 5)
    assert c["positive_pct"] == 67
    txt = studies.text(studies.report(store, now))
    assert "S5 · are the reads' setups right more often than chance? 20 separate bets of 60" in txt
    assert "S3 · conviction bets, live: 12 of 30 scored at 30 days, from 4 traders (10 needed); 13 logged from 5" in txt


def test_the_bot_answers_studies(tmp_path, monkeypatch):
    from synth import bot, service
    sent = []

    async def fake(token, chat_id, text, buttons=None):
        sent.append(text)
        return {"message_id": 1}

    monkeypatch.setattr(notify, "telegram", fake)
    s = Settings(db_path=str(tmp_path / "t.db"), telegram_token="t", telegram_chat_id="1")
    agent = service.Agent(s, Store(s.db_path))
    asyncio.run(bot.handle(agent, {"message": {"chat": {"id": 1}, "text": "/studies"}}))
    assert sent and sent[-1].startswith("Studies: each against its declared bar")
    assert any(c["command"] == "studies" for c in bot.COMMANDS) and "/studies" in bot.HELP
    assert time.time()      # the projection used the clock, not a fixed date
