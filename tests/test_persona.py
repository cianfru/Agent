import asyncio

from synth import bot, notify, orbio, persona, service
from synth.config import Settings
from synth.store import Store

NOW = 1_791_710_000.0
LEVEL = {"id": "BTC-20261013-A-80000-86000", "k": 83000.0, "lo": 80000.0, "hi": 86000.0, "fair": 0.6,
         "state": "open", "ladder": 7,
         "yes": {"state": "open", "buy": 0.6511, "sell": 0.62, "size": 2000.0},
         "no": {"state": "open", "buy": 0.3888, "sell": 0.36, "size": 2000.0}}
QUESTIONS = {"BTC": {
    "index": 83771.4, "thin": False,
    "dates": [
        {"expiry": "20261012", "label": "Mon 12 Oct", "days": 0.9, "board": False, "headline": "x", "levels": []},
        {"expiry": "20261013", "label": "Tue 13 Oct", "days": 1.9, "board": True, "headline": LEVEL["id"],
         "levels": [LEVEL], "traders": {"status": "too_few_wallets"}},
        {"expiry": "20261030", "label": "End of Oct · Fri 30 Oct", "days": 18.9, "board": True, "headline": "y",
         "levels": [], "traders": {"state": "up", "score": 0.766, "long": 31, "short": 33, "net_delta_usd": 4175293.3,
                                   "gross_delta_usd": 5452052.1, "status": "ready"}}],
    "settled": [{"expiry": "20261011", "settle_ts": NOW - 3600, "label": "Sun 11 Oct", "settle_price": 84120.5,
                 "levels": [{"id": "A", "k": 81000.0, "lo": 78000.0, "hi": 84000.0, "paid": 1.0},
                            {"id": "B", "k": 87000.0, "lo": 84000.0, "hi": 90000.0, "paid": 0.02}]}]}}
HISTORIES = {"BTC-20261011": {"ts": [NOW - 3600 - 2 * 86400, NOW - 3600 - 86400, NOW - 3600],
                              "rows": {"A": {"yb": [0.2, 0.31, 0.99], "nb": [0.8, 0.7, 0.02]},
                                       "B": {"yb": [0.1, 0.05, 0.03], "nb": [0.9, 0.95, 0.98]}}}}
MARKETS = {"coins": [{"und": "BTC", "price": 83771.4, "options": {"levels": {
    "call_wall": 95000.0, "call_wall_oi": 365.49, "put_wall": 55000.0, "put_wall_oi": 268.67}}}],
    "consensus_detail": {"1d": {"counts": {"markup": 12, "total": 15}}},
    "context": {"sentiment": {"fear_greed_value": 61, "fear_greed_label": "Greed"}}}
TRADERS = {"ranked_total": 5193, "traders": [{
    "address": "0xaf0FDd39e5D92499B0eD9F68693DA99C0ec1e92e", "rank": 1, "class": "directional", "option_pnl": 10051791.1,
    "win_rate": 0.564, "expired": 392, "first": "2024-06-28", "maker_share": 0.039, "pnl_cohort": "Money Printer",
    "size_cohort": "Leviathan", "coins": {"BTC": 75e6, "HYPE": 59e6, "ETH": 23e6}, "open_count": 51,
    "lean": {"state": "up"}}]}
DATA = {"markets": MARKETS, "traders": TRADERS, "questions": QUESTIONS, "histories": HISTORIES}


def by_kind(stories):
    return {s["kind"]: s for s in stories}


def test_stories_carry_only_formatted_facts():
    st = by_kind(persona.find_stories(DATA, [], [], NOW))
    sal = st["saloon"]["facts"]
    assert sal["date"] == "Tue 13 Oct" and sal["price_now"] == "$83,771"
    assert sal["higher"] == {"costs": "65¢ per $1", "pays_full_from": "$86K", "move_needed": "+2.7%"}
    assert sal["lower"] == {"costs": "39¢ per $1", "pays_full_at_or_below": "$80K", "move_needed": "-4.5%"}
    lean = st["lean"]
    assert lean["facts"]["wallets_short"] == 33 and lean["facts"]["money_points"] == "long"
    assert lean["angles"] == ["more wallets on one side, more money on the other"]
    assert st["walls"]["facts"]["call_pile_distance"] == "+13.4%" and st["walls"]["facts"]["put_pile_size"] == "269 BTC of open puts"
    w = st["wallet"]["facts"]
    assert w["codename"] == "Vivid Osprey" and w["options_profit"] == "$10.1M" and w["trading_since"] == "Jun 2024"
    assert "0x" not in str(w)
    s = st["settled"]["facts"]                      # Lower on B paid in full; it cost 95c a day out, Higher on A 31c
    assert s["answer"] == "Higher on $78K to $84K" and s["cost_a_day_before"] == "31¢ per $1" and s["times_the_money"] == "3.2x"
    assert st["herd"]["facts"]["coins_trending_up"] == 12


def test_hyperliquid_stories_from_outriders_alerts():
    alerts = [{"ts": NOW - 600, "body": {"coin": "STRK", "side": "short", "value": 156000, "x": 3.1, "lev": 8.0,
                                         "record": "proven: 8 trades held 3+ days, 6 won"}}]
    builds = [{"ts": NOW - 3600, "body": {"coin": "XPL", "side": "long", "traders": 4, "added_usd": 400000, "move": -0.02}}]
    st = by_kind(persona.hyperliquid_stories(alerts, builds))
    assert st["conviction"]["facts"]["size"] == "$156K" and st["conviction"]["cowboy"] is False
    assert st["build"]["facts"]["price_moved"] == "-2.0%"
    assert persona.hyperliquid_stories([{"ts": 1, "body": {"coin": "X", "side": "long"}}], []) == []   # older alerts


def test_rotation():
    st = persona.find_stories(DATA, [], [], NOW)
    first = persona.choose(st, [], NOW)
    assert first["kind"] == "settled"                                         # fresh results weigh most
    recent = [{"ts": NOW - 600, "story": {"kind": "settled", "coin": "BTC", "key": first["key"]}}]
    second = persona.choose(st, recent, NOW)
    assert second["kind"] == "wallet"                                         # not BTC again straight away
    recent.append({"ts": NOW - 300, "story": {"kind": "wallet", "coin": None, "key": second["key"]}})
    third = persona.choose(st, recent, NOW)
    assert third["kind"] == "lean"
    wallet = next(s for s in st if s["kind"] == "wallet")
    told = [{"ts": NOW - 10 * 86400, "story": {"kind": "wallet", "key": wallet["key"]}}]
    assert persona.choose([wallet], told, NOW) is None                       # a trader is not featured twice in 3 weeks


def test_lint():
    story = by_kind(persona.find_stories(DATA, [], [], NOW))["saloon"]
    good = "BTC Tuesday, 08:00 UTC: Higher costs 65¢ for a dollar that needs +2.7%. Lower costs 39¢ and needs -4.5%. Priced off @derivexyz options."
    assert persona.lint(good, story) == []
    assert any("forecast" in p or "forbidden" in p for p in persona.lint(good + " It'll run.", story))
    assert any("forbidden" in p for p in persona.lint(good.replace("Priced", "Will be priced"), story))
    assert "emoji" in persona.lint(good + " \U0001F920", story)
    assert any("numbers not in the facts: 90" in p for p in persona.lint(good.replace("+2.7%", "90%"), story))
    assert persona.lint(good.replace("90%", "90%"), story, check_numbers=False) == []
    assert any("exactly once" in p for p in persona.lint(good.replace("@derivexyz ", ""), story))
    assert any("private" in p for p in persona.lint(good + " via Reflex", story))
    assert any("mention" in p for p in persona.lint(good + " @someone", story))
    assert any("address" in p for p in persona.lint(good + " 0xaf0fdd39e5", story))
    assert any("hashtag" in p for p in persona.lint(good + " #BTC", story))


class FakeWriter:
    def __init__(self, texts):
        self.texts, self.tasks = list(texts), []

    def write(self, system, task):
        self.tasks.append(task)
        return self.texts.pop(0), {"input_tokens": 1}


def agent_with(tmp_path, monkeypatch, texts, **settings):
    sent = []

    async def fake_text(token, chat, text, buttons=None):
        sent.append({"text": text, "buttons": buttons})
        return {"message_id": 100 + len(sent)}

    async def fake_tg(token, method, **params):
        sent.append({"method": method, **params})
        return {}

    monkeypatch.setattr(notify, "telegram", fake_text)
    monkeypatch.setattr(notify, "tg", fake_tg)
    s = Settings(db_path=str(tmp_path / "t.db"), telegram_token="t", telegram_chat_id="1", **settings)
    agent = service.Agent(s, Store(s.db_path), persona_writer=FakeWriter(texts))
    agent.positions.enabled = False
    return agent, sent


WALLET_POST = "Vivid Osprey: #1 of 5,193 on @derivexyz options. $10.1M since Jun 2024, 56% of 392 settled bets won."
SETTLED_POST = "BTC settled at $84,120 Sunday. Higher on $78K to $84K cost 31¢ a day out and paid $1: 3.2x. @derivexyz"


def test_draft_retries_once_then_goes_to_telegram_with_buttons(tmp_path, monkeypatch):
    agent, sent = agent_with(tmp_path, monkeypatch, ["Higher will pay 3.2x. @derivexyz", SETTLED_POST])
    res = asyncio.run(agent.persona_post(data=DATA, now=NOW))
    assert res["status"] == "draft" and res["story"].startswith("settled:BTC")
    assert "rejected" in agent.persona.tasks[1] and "forbidden word" in agent.persona.tasks[1]
    p = agent.store.get_post(res["id"])
    assert p["text"] == SETTLED_POST and p["story"]["kind"] == "settled" and p["tg_msg"] == 101
    assert sent[0]["buttons"]["inline_keyboard"][0][0]["callback_data"] == f"pp:{res['id']}"
    # the next slot tells another story
    agent.persona.texts = [WALLET_POST]
    res2 = asyncio.run(agent.persona_post(data=DATA, now=NOW + 600))
    assert agent.store.get_post(res2["id"])["story"]["kind"] != "settled"


def test_two_bad_drafts_are_rejected_not_sent(tmp_path, monkeypatch):
    agent, sent = agent_with(tmp_path, monkeypatch, ["moon soon", "to the moon"])
    res = asyncio.run(agent.persona_post(data=DATA, now=NOW))
    assert res["status"] == "rejected" and not sent


def test_post_skip_and_reply_edit_in_telegram(tmp_path, monkeypatch):
    agent, sent = agent_with(tmp_path, monkeypatch, [SETTLED_POST])
    pid = asyncio.run(agent.persona_post(data=DATA, now=NOW))["id"]
    # a reply to the draft replaces its text (the owner's numbers are not checked against the facts)
    reply = {"message": {"chat": {"id": 1}, "text": "BTC settled at $84,120. A 31¢ ticket paid $1. @derivexyz 10/10",
                         "reply_to_message": {"message_id": 101}}}
    asyncio.run(bot.handle(agent, reply))
    p = agent.store.get_post(pid)
    assert p["text"].endswith("10/10") and p["story"]["edited"] and p["tg_msg"] == 102
    bad = {"message": {"chat": {"id": 1}, "text": "it will pump", "reply_to_message": {"message_id": 102}}}
    asyncio.run(bot.handle(agent, bad))
    assert sent[-1]["text"].startswith("Not saved") and agent.store.get_post(pid)["text"].endswith("10/10")

    published = []

    async def fake_post_x(key, text, max_cost=0.03, client=None, base=None):
        published.append(text)
        return {"status": "published", "post_id": "x1", "url": "https://x.com/outrider/status/1"}

    monkeypatch.setattr(orbio, "post_x", fake_post_x)
    tap = {"callback_query": {"id": "cq", "data": f"pp:{pid}", "message": {"message_id": 102, "chat": {"id": 1}}}}
    asyncio.run(bot.handle(agent, tap))
    assert published == [p["text"]] and agent.store.get_post(pid)["status"] == "published"
    assert sent[-1]["text"] == f"Posted #{pid}: https://x.com/outrider/status/1"
    asyncio.run(bot.handle(agent, tap))
    assert len(published) == 1                                               # never twice

    agent.persona.texts = [WALLET_POST]
    pid2 = asyncio.run(agent.persona_post(data=DATA, now=NOW + 600))["id"]
    asyncio.run(bot.handle(agent, {"callback_query": {"id": "c2", "data": f"px:{pid2}",
                                                      "message": {"message_id": 103, "chat": {"id": 1}}}}))
    assert agent.store.get_post(pid2)["status"] == "skipped"


def test_failed_post_keeps_its_buttons_and_commands_are_not_edits(tmp_path, monkeypatch):
    agent, sent = agent_with(tmp_path, monkeypatch, [SETTLED_POST])
    pid = asyncio.run(agent.persona_post(data=DATA, now=NOW))["id"]

    async def no_account(key, text, max_cost=0.03, client=None, base=None):
        raise orbio.OrbioError("connect", "X account not connected in the Orbio dashboard (409)")

    monkeypatch.setattr(orbio, "post_x", no_account)
    asyncio.run(bot.handle(agent, {"callback_query": {"id": "c", "data": f"pp:{pid}",
                                                      "message": {"message_id": 101, "chat": {"id": 1}}}}))
    assert agent.store.get_post(pid)["status"] == "failed" and sent[-1]["text"].startswith("Not posted: X account")
    assert not any(m.get("method") == "editMessageReplyMarkup" for m in sent)
    asyncio.run(bot.handle(agent, {"message": {"chat": {"id": 1}, "text": "/help", "reply_to_message": {"message_id": 101}}}))
    assert agent.store.get_post(pid)["text"] == SETTLED_POST


def test_open_winner_story_and_three_week_rule():
    winners = [{"address": "0x" + "1" * 40, "codename": "Amber Lemur", "coin": "ENA", "side": "long", "upnl": 318_862,
                "move": 1.74, "roe": 5.2, "held_days": 89.4, "since": "2026-07-15", "share_of_account": 0.8,
                "as_of": NOW - 600},
               {"address": "0x" + "2" * 40, "codename": "Quiet Crow", "coin": "W", "side": "long", "upnl": 20_000,
                "move": 0.6, "held_days": 40, "since": "2026-09-01", "as_of": NOW},    # under $50K: no story
               {"address": "0x" + "3" * 40, "codename": "Old Read", "coin": "SOL", "side": "long", "upnl": 900_000,
                "move": 2.0, "held_days": 60, "since": "2026-08-01", "as_of": NOW - 7 * 3600}]   # read too long ago
    st = persona.open_winner_stories(winners, NOW)
    assert len(st) == 1 and st[0]["cowboy"] is False and st[0]["coin"] == "ENA"
    f = st[0]["facts"]
    assert (f["held"], f["since"], f["up_on_price"], f["on_margin"], f["open_profit"], f["of_the_account"]) == (
        "89 days", "15 Jul", "+174.0%", "+520.0%", "$319K", "80%")
    told = [{"ts": NOW - 10 * 86400, "story": {"kind": "open_winner", "key": st[0]["key"]}}]
    assert persona.choose(st, told, NOW) is None
