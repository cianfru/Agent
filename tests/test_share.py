"""Signals shared to a channel others read: everything sent to him that others may see, nothing that is his."""

import asyncio
import json
import time

from synth import analyst, bot, chat, notify, service
from synth.config import Settings
from synth.position_traders import PositionTraders
from synth.store import Store
from tests.test_analyst import READ, RECORD
from tests.test_chat import FakeClient, response, text_block

CHANNEL = "-1001234567890"


def make(tmp_path, monkeypatch, responses=(), channel=CHANNEL, **kw):
    sent = []

    async def fake(token, chat_id, text, buttons=None, silent=False):
        sent.append({"chat": str(chat_id), "text": text, "buttons": buttons})
        return {"message_id": len(sent)}

    monkeypatch.setattr(notify, "telegram", fake)
    tmp_path.mkdir(parents=True, exist_ok=True)
    s = Settings(db_path=str(tmp_path / "t.db"), telegram_token="t", telegram_chat_id="1", telegram_channel_id=channel,
                 **kw)
    store = Store(s.db_path)
    agent = service.Agent(s, store)
    agent.positions = PositionTraders(s, store, seed={"as_of": "2026-10-10", "traders": []})
    agent.positions.enabled = False
    agent.tracker.last_mids, agent.tracker.last_mids_ts = {"SUI": 1.8, "BTC": 98_000.0}, time.time()
    client = FakeClient(list(responses))
    agent.chat = chat.Chat(agent, "claude-opus-5-5", client=client)
    agent.analyst = analyst.Analyst(agent, "claude-opus-5-5", client=client, quiet="")

    async def no_pictures(*a, **k):
        return []

    monkeypatch.setattr(agent.analyst, "pictures", no_pictures)
    return agent, sent


def test_a_signal_reaches_him_then_the_channel_without_buttons(tmp_path, monkeypatch):
    agent, sent = make(tmp_path, monkeypatch)
    buttons = notify.follow_buttons(7)
    asyncio.run(notify.push(agent.s, agent.store, "Group: 4 traders long SOL", "group", buttons, public="Group: 4 "
                            "traders long SOL"))
    assert [m["chat"] for m in sent] == ["1", CHANNEL]
    assert sent[0]["buttons"] == buttons and sent[1]["buttons"] is None
    assert agent.store.count_briefs("share", time.time() - 60) == 1
    asyncio.run(notify.push(agent.s, agent.store, "status note", "status"))          # nothing public: his alone
    assert sent[-1]["chat"] == "1" and len(sent) == 3


def test_nothing_held_back_or_unconfigured_is_shared(tmp_path, monkeypatch):
    agent, sent = make(tmp_path, monkeypatch, max_pushes_per_day=1)
    asyncio.run(notify.push(agent.s, agent.store, "first", "smart", public="first"))
    asyncio.run(notify.push(agent.s, agent.store, "second", "smart", public="second"))   # held by his daily cap
    assert [m["text"] for m in sent] == ["first", "first"]
    agent2, sent2 = make(tmp_path / "b", monkeypatch, channel="")
    asyncio.run(notify.push(agent2.s, agent2.store, "x", "smart", public="x"))
    assert [m["chat"] for m in sent2] == ["1"]


def test_a_refused_channel_post_is_kept_for_status(tmp_path, monkeypatch):
    agent, sent = make(tmp_path, monkeypatch)

    async def refuse(token, chat_id, text, buttons=None, silent=False):
        if str(chat_id) == CHANNEL:
            notify.LAST_ERROR["text"] = "Forbidden: bot is not a member of the channel chat"
            return None
        return {"message_id": 1}

    monkeypatch.setattr(notify, "telegram", refuse)
    asyncio.run(notify.push(agent.s, agent.store, "x", "smart", public="x"))
    failed = agent.store.briefs_since("share_failed", time.time() - 60)
    assert failed and "not a member" in failed[0]["body"]["error"]
    assert "Channel: 0 shared in 24h, 1 failed: Forbidden: bot is not a member" in bot.status_text(agent)


def test_a_read_is_shared_without_his_lines_and_never_when_it_speaks_of_his_book(tmp_path, monkeypatch):
    agent, sent = make(tmp_path, monkeypatch, [response([text_block(json.dumps(READ))])])
    agent.store.add_brief("push_held", {"kind": "smart", "text": "held"})
    asyncio.run(agent.analyst.run("morning", time.time()))
    mine, public = sent[0]["text"], sent[1]["text"]
    assert sent[1]["chat"] == CHANNEL and "Held back by the daily cap" in mine and "/held" not in public
    assert READ["headline"] in public
    task = agent.analyst.client.calls[0]["messages"][0]["content"]
    assert "posted word for word to a channel his friends read" in task
    own = {**READ, "read": READ["read"] + " Your SUI short is already in profit."}
    agent.analyst.client.responses = [response([text_block(json.dumps(own))])]
    n = len(sent)
    asyncio.run(agent.analyst.run("asked", time.time() + 10))
    assert [m["chat"] for m in sent[n:]] == ["1"]                                  # his alone
    skipped = agent.store.briefs_since("share_skipped", time.time() - 60)
    assert skipped and skipped[0]["body"]["why"].lower().startswith("your sui short")


def test_the_words_that_speak_of_his_book():
    hit = lambda text: analyst.mentions_his({"headline": "", "read": text, "views": []})  # noqa: E731
    for t in ("Your SOL long sits on the band.", "you're long ETH into this", "You hold HYPE", "your position",
              "the watch list has two coins", "his long is fine"):
        assert hit(t), t
    for t in ("Four proven traders added SUI shorts.", "Longs are crowded.", "If you want a long, wait for 1.95.",
              "Your record: 12 setups scored."):
        assert not hit(t), t


def test_an_alert_against_his_position_is_never_shared(tmp_path, monkeypatch):
    agent, sent = make(tmp_path, monkeypatch, pushes=("conviction", "build"))
    calls = []

    async def fake_push(s, store, text, kind, *a, public=None, **k):
        calls.append((kind, text, public))
        return {"message_id": 1}

    monkeypatch.setattr(notify, "push", fake_push)
    t = {"address": "0x" + "a" * 40, **RECORD}
    p = {"coin": "SOL", "side": "short", "value": 500_000, "entry_px": 150.0, "size": 3333, "notional": 500_000}
    monkeypatch.setattr(agent.positions, "against_his", lambda coin, side: "")
    asyncio.run(agent.positions._conviction(t, 1, None, p, 2.0))
    kind, text, public = calls[-1]
    assert kind == "conviction" and public == text                       # nothing of his: shared as he got it
    monkeypatch.setattr(agent.positions, "against_his", lambda coin, side: "\nThis goes against your SOL long.")
    asyncio.run(agent.positions._conviction(t, 2, None, p, 2.0))
    kind, text, public = calls[-1]
    assert "against your SOL long" in text and public is None


def test_added_to_a_channel_he_alone_is_told_its_id(tmp_path, monkeypatch):
    agent, sent = make(tmp_path, monkeypatch, channel="")
    up = {"my_chat_member": {"chat": {"id": -100555, "title": "Outrider signals", "type": "channel"},
                             "from": {"username": "andrea"}, "new_chat_member": {"status": "administrator"}}}
    asyncio.run(bot.handle(agent, up))
    assert [m["chat"] for m in sent] == ["1"]
    assert "Its id is -100555" in sent[0]["text"] and "TELEGRAM_CHANNEL_ID" in sent[0]["text"]
    up["my_chat_member"]["new_chat_member"]["status"] = "member"
    asyncio.run(bot.handle(agent, up))
    assert "not as an admin" in sent[-1]["text"]
    agent2, sent2 = make(tmp_path / "b", monkeypatch, channel="-100555")
    up["my_chat_member"]["new_chat_member"]["status"] = "administrator"
    asyncio.run(bot.handle(agent2, up))
    assert sent2[-1]["text"].startswith("I'm in Outrider signals")
