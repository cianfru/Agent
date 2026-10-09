import asyncio
import datetime as dt

import respx

from synth import post, service
from synth.config import Settings
from synth.post import DeskPost
from synth.store import Store

DAY = dt.date(2026, 10, 9)


def fields(**kw):
    base = dict(
        regime_line="Breadth: 80% of coins trending up, F&G 59",
        smart_money_line="Top HL traders 7 long / 2 short HYPE; Derive whales net long BTC $3.4M",
        setup_coin="HYPE", setup_direction="long",
        setup_line="Engine trending up 4h+1d, 30d skew toward calls.",
        invalidation_line="1d close back under 38",
        chain_line="Robinhood Chain: 7 of 1,189 Orbio launches graduated this week",
    )
    base.update(kw)
    return DeskPost(**base)


def test_lint_catches_forecast_words_links_and_length():
    ok = "\nNot advice."
    assert post.lint("BTC will rip" + ok) == ['forbidden word "will"']
    assert "link" in post.lint("see cowboy.trading" + ok)[0]
    assert post.lint("x" * 281) == ["length 281 > 280", "missing the not-advice footer"]
    assert post.lint("Breadth 80% trending up" + ok) == []


def test_lint_catches_jargon_and_missing_footer():
    assert post.lint("BTC STRONG_LONG, rr25 -0.015\nNot advice.") == ['internal jargon "STRONG_LONG": use plain words']
    assert post.lint("BTC trending up") == ["missing the not-advice footer"]


def test_assemble_fits_and_numbers_the_pick():
    text = post.assemble(fields(), 12, DAY)
    assert len(text) <= 280 and post.lint(text) == []
    assert text.startswith("Desk read 9 Oct")
    assert "Setup #12: $HYPE long bias." in text and "Off if: 1d close back under 38" in text


def test_footer_never_dropped():
    for f in (fields(), fields(smart_money_line="y" * 75, setup_line="z" * 60, regime_line="w" * 55)):
        assert post.assemble(f, 1, DAY).splitlines()[-1] in (post.FOOTER, post.SHORT_FOOTER)


def test_assemble_drops_optional_lines_when_long():
    long = fields(smart_money_line="y" * 75, setup_line="z" * 60, regime_line="w" * 55)
    text = post.assemble(long, 3, DAY)
    assert "Robinhood Chain" not in text
    assert len(text) <= 280


def test_assemble_too_long_is_left_for_lint():
    text = post.assemble(fields(setup_line="z" * 200), 3, DAY)
    assert post.lint(text)[0].startswith("length")


def test_no_setup():
    text = post.assemble(fields(setup_coin=None, setup_direction="none"), None, DAY)
    assert "No setup today" in text and "Setup #" not in text


def test_scorecard_counts_misses():
    picks = [
        {"id": 1, "coin": "BTC", "direction": "long", "ret_7d": 0.05},
        {"id": 2, "coin": "ETH", "direction": "short", "ret_7d": -0.02},
        {"id": 3, "coin": "SOL", "direction": "long", "ret_7d": None},
    ]
    text = post.scorecard(picks, DAY)
    assert "Setups scored at 7 days: 2" in text and "Right direction: 1/2" in text
    assert "Worst: #2 $ETH short -2.0%" in text
    assert post.lint(text) == []
    assert post.scorecard([picks[2]], DAY) is None


class FakeSynth:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.feedback = []

    def desk_post(self, digest, feedback=""):
        self.feedback.append(feedback)
        return self.outputs.pop(0), {"input_tokens": 1}


def agent_with(tmp_path, outputs, **settings):
    s = Settings(db_path=str(tmp_path / "t.db"), orbio_api_key="k", **settings)
    a = service.Agent(s, Store(s.db_path), FakeSynth(outputs))
    from tests.test_service import FakeHL
    a.tracker._hl = FakeHL()

    async def collect():
        return {"cowboy": {"coins": {"HYPE": {"price": 40.0}}, "market": {}}}

    async def no_block0(*_):
        raise RuntimeError("offline")

    a.collect = collect
    service.block0.fetch = no_block0
    return a


def test_draft_then_approve_publishes_once(tmp_path):
    a = agent_with(tmp_path, [fields()])
    out = asyncio.run(a.desk_post())
    assert out["status"] == "draft" and out["pick_id"]
    pick = a.store.picks(origin="post")[0]
    assert pick["coin"] == "HYPE" and pick["entry_price"] == 40.0

    with respx.mock:
        route = respx.post("https://api.orbio.so/api/v1/tools/social.post").respond(200, json={
            "result": {"post_id": "p1", "status": "published",
                       "platforms": [{"platformPostUrl": "https://x.com/desk/status/1"}]},
            "cost": {"credit": "0.0187"}})
        done = asyncio.run(a.publish_post(out["id"]))
        again = asyncio.run(a.publish_post(out["id"]))
    assert done["status"] == "published" and done["url"].endswith("/1")
    assert again["status"] == "published" and route.call_count == 1
    sent = route.calls[0].request
    assert sent.headers["authorization"] == "Bearer k" and b'"platforms":["x"]' in sent.content.replace(b" ", b"")


def test_bad_draft_is_retried_then_rejected_and_pick_removed(tmp_path):
    bad = fields(setup_line="This will run higher.")
    a = agent_with(tmp_path, [bad, bad])
    out = asyncio.run(a.desk_post())
    assert out["status"] == "rejected" and 'forbidden word "will"' in out["problems"][0]
    assert a.synth.feedback[1].startswith('forbidden word "will"')
    assert a.store.picks(origin="post") == []


def test_retry_fixes_draft(tmp_path):
    a = agent_with(tmp_path, [fields(setup_line="This will run higher."), fields()])
    out = asyncio.run(a.desk_post())
    assert out["status"] == "draft" and len(a.store.picks(origin="post")) == 1


def test_orbio_not_connected_marks_failed(tmp_path):
    a = agent_with(tmp_path, [fields()])
    with respx.mock:
        respx.post("https://api.orbio.so/api/v1/tools/social.post").respond(409, json={})
        out = asyncio.run(a.desk_post(publish=True))
    assert out["status"] == "failed" and "not connected" in out["error"]


def test_desk_setups_numbered_apart_from_alert_picks(tmp_path):
    a = agent_with(tmp_path, [fields(), fields(setup_coin="BTC")])
    for _ in range(5):
        a.store.add_pick("alert", "ETH", "long", 3, 2500.0, "x", {})
    first = asyncio.run(a.desk_post())
    second = asyncio.run(a.desk_post())
    assert "Setup #1: $HYPE" in first["text"] and "Setup #2: $BTC" in second["text"]
