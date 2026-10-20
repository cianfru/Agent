import asyncio
import json
from types import SimpleNamespace as NS

from synth import bot, chat, notify, service
from synth.config import Settings
from synth.store import Store

NOW = 1_791_710_000.0


def text_block(t):
    return NS(type="text", text=t)


def tool_block(name, args, bid="t1"):
    return NS(type="tool_use", id=bid, name=name, input=args)


def response(blocks, stop="end_turn", model="claude-opus-5-5"):
    return NS(content=blocks, stop_reason=stop, model=model,
              usage=NS(input_tokens=10_000, output_tokens=500, cache_read_input_tokens=0, cache_creation_input_tokens=0))


class FakeClient:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
        self.messages = self.beta = self
        self.beta.messages = self

    def create(self, **params):
        self.calls.append(json.loads(json.dumps(params, default=lambda o: o.__dict__)))
        return self.responses.pop(0)


def make(tmp_path, responses, daily=2.0):
    s = Settings(db_path=str(tmp_path / "t.db"), telegram_token="t", telegram_chat_id="1")
    agent = service.Agent(s, Store(s.db_path))
    agent.positions.enabled = False
    agent.store.put_snapshot("reflex", {"positions": {"HYPE": [12, 4_000_000, 3, 900_000, 41.2]}})
    agent.chat = chat.Chat(agent, "claude-opus-5-5", daily_usd=daily, client=FakeClient(responses))
    return agent


def test_answers_from_the_data_through_tools(tmp_path):
    agent = make(tmp_path, [response([tool_block("coin", {"coin": "hype"})], stop="tool_use"),
                            response([text_block("HYPE: 12 profitable traders long, 3 short.")])])
    answer = agent.chat.run("1", "how is HYPE?", NOW)
    assert answer == "HYPE: 12 profitable traders long, 3 short."
    first, second = agent.chat.client.calls
    assert first["fallbacks"] == "default" and first["output_config"] == {"effort": "medium"}
    assert [t["name"] for t in first["tools"]][:2] == ["market_overview", "coin"]
    result = json.loads(second["messages"][-1]["content"][0]["content"])
    assert result["coin"] == "HYPE" and result["profitable_traders"]["long_n"] == 12
    # the next question carries the last exchange as plain text, nothing else
    agent.chat.client.responses = [response([text_block("Still 12.")])]
    agent.chat.run("1", "and now?", NOW + 60)
    msgs = agent.chat.client.calls[-1]["messages"]
    assert msgs == [{"role": "user", "content": "how is HYPE?"},
                    {"role": "assistant", "content": "HYPE: 12 profitable traders long, 3 short."},
                    {"role": "user", "content": "and now?"}]
    assert abs(agent.chat.spent_today(NOW) - 3 * (10_000 * 4 + 500 * 20) / 1e6) < 1e-9     # $0.05 per call here


def test_daily_budget_stops_before_calling_the_model(tmp_path):
    agent = make(tmp_path, [response([text_block("ok")]), response([text_block("new day")])], daily=0.01)
    agent.chat.run("1", "hi", NOW)
    calls = len(agent.chat.client.calls)
    assert "budget" in agent.chat.run("1", "again", NOW + 10) and len(agent.chat.client.calls) == calls
    assert agent.chat.run("1", "next day", NOW + 86400) == "new day"                    # resets at 00:00 UTC


def test_notes_live_in_the_system_prompt(tmp_path):
    agent = make(tmp_path, [response([tool_block("remember", {"note": "Holds ENA long from 0.09"})], stop="tool_use"),
                            response([text_block("Noted.")]), response([text_block("You hold ENA.")])])
    agent.chat.run("1", "remember I hold ENA long from 0.09", NOW)
    agent.chat.run("1", "what do I hold?", NOW + 5)
    system = agent.chat.client.calls[-1]["system"][0]["text"]
    assert "1. Holds ENA long from 0.09" in system
    assert json.loads(agent.chat.execute("forget", {"note_id": 1}, NOW)) == {"dropped": True}
    assert agent.store.chat_notes() == []


def test_unknown_trader_and_tool_errors_come_back_as_data(tmp_path):
    agent = make(tmp_path, [])
    assert json.loads(agent.chat.execute("trader", {"query": "0xnothere"}, NOW))["found"] is False
    assert "error" in json.loads(agent.chat.execute("open_winners", {"sort": "nonsense"}, NOW))


def test_telegram_plain_messages_are_questions(tmp_path, monkeypatch):
    sent = []

    async def fake_text(token, chat_id, text, buttons=None):
        sent.append(text)
        return {"message_id": 1}

    async def fake_tg(token, method, **params):
        sent.append(method)
        return {}

    monkeypatch.setattr(notify, "telegram", fake_text)
    monkeypatch.setattr(notify, "tg", fake_tg)
    agent = make(tmp_path, [response([text_block("x" * 5000)])])
    asyncio.run(bot.handle(agent, {"message": {"chat": {"id": 1}, "text": "what's moving?"}}))
    assert sent[0] == "sendChatAction" and sent[1] == "x" * 3900 and sent[2] == "x" * 1100
    asyncio.run(bot.handle(agent, {"message": {"chat": {"id": 1}, "text": "/cost"}}))
    assert sent[-1].startswith("Talking today: $0.") and "claude-opus-5-5" in sent[-1]
    asyncio.run(bot.handle(agent, {"message": {"chat": {"id": 1}, "text": "/reset"}}))
    assert agent.store.chat_history("1", 12) == []
    agent.chat = None
    asyncio.run(bot.handle(agent, {"message": {"chat": {"id": 1}, "text": "hello"}}))
    assert sent[-1].startswith("I can't talk yet")
