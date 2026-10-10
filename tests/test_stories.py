import datetime as dt

from synth import stories

REFLEX = {
    "totals": {"long_usd": 48e6, "short_usd": 61e6, "long_n": 180, "short_n": 212, "opens_long_24h": 18,
               "opens_short_24h": 41, "day_ago_long_n": 0, "day_ago_short_n": 0, "day_ago_known": False},
    "coins": {
        "STRK": {"opens_24h": {"long": 1, "short": 6}},
        "PUMP": {"opens_24h": {"long": 7, "short": 1}},
        "SOL": {"opens_24h": {"long": 4, "short": 0}},     # smart opens but nothing else agrees
        "DOGE": {"opens_24h": {"long": 0, "short": 0}},
    },
    "anomalies": [
        {"coin": "STRK", "type": "OI_SURGE", "direction": "LONG", "value": 34.0},
        {"coin": "STRK", "type": "EXTREME_FUNDING", "direction": "SHORT", "value": -0.002},
        {"coin": "PUMP", "type": "CVD_EXTREME", "direction": "LONG", "value": 1.9},
        {"coin": "DOGE", "type": "VOLUME_SPIKE", "direction": "NEUTRAL", "value": 6.5},  # alone: never shown
    ],
}
GROUP = ({"id": 7, "venue": "hyperliquid", "coin": "STRK", "side": "short", "meta": {"span_h": 3.0}},
         [{"address": a} for a in ("0xa", "0xb", "0xc", "0xd")])


def test_sentiment_lean():
    s = stories.sentiment(REFLEX)
    assert s["lean"] == "short"
    assert stories.sentiment({"totals": {**REFLEX["totals"], "short_usd": 50e6}})["lean"] == "balanced"
    assert stories.sentiment({}) is None


def test_stories_need_smart_money_plus_agreement():
    found = stories.coin_stories(REFLEX, [GROUP], [])
    by = {s["coin"]: s for s in found}
    assert set(by) == {"STRK", "PUMP"}  # SOL: smart opens alone; DOGE: volume spike alone
    assert by["STRK"]["text"] == ("STRK short · shorts piling in · 4 smart shorts in 3h (#7) · "
                                  "6 new shorts vs 1 today · open interest +34% · funding negative: shorts crowded")
    assert by["PUMP"]["text"] == "PUMP long · buyers in control · 7 new longs vs 1 today · aggressive buying"
    assert found[0]["coin"] == "STRK"


def test_split_smart_money_is_left_out():
    events = [{"coin": "PUMP", "kind": "wallet_move", "direction": "short", "title": "big short (cowboy)"},
              {"coin": "PUMP", "kind": "wallet_move", "direction": "short", "title": "big short 2 (cowboy)"}]
    found = stories.coin_stories(REFLEX, [], events)
    assert "PUMP" not in {s["coin"] for s in found}  # 2 (opens) vs 1+1 (moves): split


def test_change_and_format():
    hist = [{"ts": 0, "long_usd": 50e6, "short_usd": 53e6}, {"ts": 14_400, "long_usd": 48e6, "short_usd": 61e6}]
    d = stories.change(hist, since_ts=0)
    assert d == {"since": 0, "long_usd": -2e6, "short_usd": 8e6}
    text = stories.format_read(stories.sentiment(REFLEX), d, [{"coin": "BTC", "lean": "long"}],
                               stories.coin_stories(REFLEX, [GROUP], []),
                               dt.datetime(2026, 10, 10, 10, 0, tzinfo=dt.timezone.utc),
                               dt.datetime(2026, 10, 10, 6, 0, tzinfo=dt.timezone.utc))
    lines = text.splitlines()
    assert lines[0] == "Outrider · 10:00 UTC · last 4h"
    assert lines[1] == "Smart money: leaning short"
    assert lines[2] == "Hyperliquid: shorts $61.0M vs longs $48.0M · opened 24h: 41 short / 18 long"
    assert lines[3] == "Since 00:00: shorts +$8.0M, longs -$2.0M"
    assert lines[4] == "Derive options: BTC long"
    assert "Where it's crowding" in lines and len(lines) <= 14
    empty = stories.format_read(None, None, [], [], dt.datetime(2026, 10, 10, 6, tzinfo=dt.timezone.utc), None)
    assert "Nothing lining up" in empty


def test_agent_market_read_from_stored_snapshots(tmp_path):
    import time

    from synth import service
    from synth.config import Settings
    from synth.store import Store
    from tests.test_service import offline

    s = Settings(db_path=str(tmp_path / "t.db"))
    agent = offline(service.Agent(s, Store(s.db_path)))
    agent.store.put_snapshot("reflex", REFLEX)
    agent.store.put_snapshot("sentiment_history", {"rows": [
        {"ts": time.time() - 4 * 3600, "long_usd": 50e6, "short_usd": 53e6},
        {"ts": time.time(), "long_usd": 48e6, "short_usd": 61e6}]})
    gid = agent.store.add_group("hyperliquid", "STRK", "short", "k", 0.07, {"span_h": 3.0})
    for a in ("0xa", "0xb"):
        agent.store.add_member(gid, {"address": a, "side": "short", "entry_px": 0.07, "opened_ts": time.time()})
    read = agent.market_read(time.time() - 4 * 3600)
    assert "Smart money: leaning short" in read["text"]
    assert "shorts +$8.0M, longs -$2.0M" in read["text"]
    assert read["stories"][0]["text"].startswith("STRK short · shorts piling in · 2 smart shorts in 3h (#1)")
