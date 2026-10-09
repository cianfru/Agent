import asyncio
from types import SimpleNamespace

from synth import bot, notify, tracking
from synth.config import Settings
from synth.store import Store


def setup(tmp_path, monkeypatch):
    calls = []

    async def fake_tg(token, method, **params):
        calls.append((method, params))
        return {}

    async def fake_text(token, chat, text, buttons=None):
        calls.append(("sendMessage", {"text": text}))

    monkeypatch.setattr(notify, "tg", fake_tg)
    monkeypatch.setattr(notify, "telegram", fake_text)
    s = Settings(db_path=str(tmp_path / "t.db"), telegram_token="t", telegram_chat_id="1390601536")
    store = Store(s.db_path)
    gid = store.add_group("hyperliquid", "STRK", "short", "k", 0.0739, {"span_h": 3})
    store.add_member(gid, {"address": "0xaaaa000000000000000000000000000000001111", "side": "short",
                           "entry_px": 0.0712, "size_usd": 42000, "opened_ts": 1})
    agent = SimpleNamespace(s=s, store=store, tracker=tracking.GroupTracker(s, store))
    return agent, gid, calls


def tap(data, chat="1390601536"):
    return {"update_id": 1, "callback_query": {"id": "cq", "data": data,
                                                "message": {"message_id": 9, "chat": {"id": int(chat)}}}}


def test_follow_button_from_owner(tmp_path, monkeypatch):
    agent, gid, calls = setup(tmp_path, monkeypatch)
    asyncio.run(bot.handle(agent, tap(f"f:{gid}")))
    assert agent.store.get_group(gid)["followed"] is True
    assert calls[0][0] == "answerCallbackQuery" and "Following #1" in calls[0][1]["text"]
    assert calls[1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"u:{gid}"
    asyncio.run(bot.handle(agent, tap(f"u:{gid}")))
    assert agent.store.get_group(gid)["followed"] is False


def test_taps_from_other_chats_are_ignored(tmp_path, monkeypatch):
    agent, gid, calls = setup(tmp_path, monkeypatch)
    asyncio.run(bot.handle(agent, tap(f"f:{gid}", chat="555")))
    assert agent.store.get_group(gid)["followed"] is False and calls == []


def test_following_command_lists_traders(tmp_path, monkeypatch):
    agent, gid, calls = setup(tmp_path, monkeypatch)
    agent.tracker.set_follow(gid, True)
    msg = {"update_id": 2, "message": {"chat": {"id": 1390601536}, "text": "/following"}}
    asyncio.run(bot.handle(agent, msg))
    text = calls[-1][1]["text"]
    assert text.splitlines()[0] == "#1 STRK short · open · following"
    assert "0xaaaa…1111 @ 0.0712 · open" in text
