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
        {"id": 1, "coin": "BTC", "direction": "long", "ret_7d": 0.05, "published_ts": 1.0},
        {"id": 2, "coin": "ETH", "direction": "short", "ret_7d": -0.02, "published_ts": None},   # drafted, not posted
        {"id": 3, "coin": "SOL", "direction": "long", "ret_7d": None},
    ]
    text = post.scorecard(picks, DAY)
    assert "Setups scored at 7 days: 2" in text and "Right direction: 1/2" in text
    assert "Worst: #2 $ETH short -2.0%" in text and "Logged but not posted: 1" in text
    assert post.lint(text) == []
    assert "not posted" not in post.scorecard([{**picks[1], "published_ts": 2.0}], DAY)
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


def test_bad_draft_is_retried_then_rejected_and_pick_voided(tmp_path):
    bad = fields(setup_line="This will run higher.")
    a = agent_with(tmp_path, [bad, bad])
    out = asyncio.run(a.desk_post())
    assert out["status"] == "rejected" and 'forbidden word "will"' in out["problems"][0]
    assert a.synth.feedback[1].startswith('forbidden word "will"')
    assert a.store.picks(origin="post") == []                        # out of every count...
    kept = a.store.picks(origin="post", include_void=True)            # ...but kept, with its reason
    assert len(kept) == 1 and kept[0]["void_reason"].startswith('draft rejected: forbidden word "will"')


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


ORBIO_POST = "https://api.orbio.so/api/v1/tools/social.post"
ORBIO_STATUS = "https://api.orbio.so/api/v1/tools/social.post.status"
PUBLISHED = {"result": {"post_id": "p1", "status": "published",
                        "platforms": [{"platformPostUrl": "https://x.com/desk/status/1"}]}}


def test_two_approvals_at_once_post_once(tmp_path):
    # a double tap, or Telegram and the dashboard at the same moment
    a = agent_with(tmp_path, [fields()])
    a.tracker.last_mids = {"HYPE": 41.0}
    out = asyncio.run(a.desk_post())

    calls = []

    async def slow_orbio(key, text, max_cost):              # Orbio takes a moment: the second tap lands meanwhile
        calls.append(text)
        await asyncio.sleep(0.05)
        return {"status": "published", "post_id": "p1", "url": "https://x.com/desk/status/1", "error": None}

    async def both():
        return await asyncio.gather(a.publish_post(out["id"]), a.publish_post(out["id"]))

    service.orbio.post_x, real = slow_orbio, service.orbio.post_x
    try:
        first, second = asyncio.run(both())
    finally:
        service.orbio.post_x = real
    assert len(calls) == 1
    assert first["status"] == "published" and second["status"] == "publishing"
    pick = a.store.picks(origin="post")[0]
    assert pick["published_ts"] and pick["published_px"] == 41.0 and pick["entry_price"] == 40.0   # both kept


def test_a_lost_answer_is_never_sent_again(tmp_path):
    import httpx
    for i, fail in enumerate((httpx.ReadTimeout("slow"), 504)):
        (tmp_path / str(i)).mkdir()
        a = agent_with(tmp_path / str(i), [fields()])
        out = asyncio.run(a.desk_post())
        with respx.mock:
            route = respx.post(ORBIO_POST)
            if isinstance(fail, int):
                route.respond(fail, json={})
            else:
                route.side_effect = fail
            res = asyncio.run(a.publish_post(out["id"]))
            again = asyncio.run(a.publish_post(out["id"]))
        assert res["status"] == "unknown" and again["status"] == "unknown" and route.call_count == 1
        assert "check X" in res["error"] or "504" in res["error"]


def test_orbio_saying_failed_is_failed_and_can_be_tried_again(tmp_path):
    a = agent_with(tmp_path, [fields()])
    out = asyncio.run(a.desk_post())
    with respx.mock:
        respx.post(ORBIO_POST).respond(200, json={"result": {"post_id": "p1", "status": "failed",
                                                             "platforms": [{"errorMessage": "duplicate"}]}})
        res = asyncio.run(a.publish_post(out["id"]))
    assert res["status"] == "failed" and res["error"] == "duplicate"
    assert a.store.picks(origin="post")[0]["published_ts"] is None
    with respx.mock:
        respx.post(ORBIO_POST).respond(200, json=PUBLISHED)
        assert asyncio.run(a.publish_post(out["id"]))["status"] == "published"


def test_a_running_post_is_settled_by_its_status_not_resent(tmp_path):
    a = agent_with(tmp_path, [fields()])
    out = asyncio.run(a.desk_post())
    with respx.mock:
        sent = respx.post(ORBIO_POST).respond(202, json={"status": "running", "result": {"post_id": "p9"}})
        status = respx.post(ORBIO_STATUS).respond(200, json={"result": {"post_id": "p9", "status": "pending"}})
        assert asyncio.run(a.publish_post(out["id"]))["status"] == "running"
        assert asyncio.run(a.settle_posts()) == 0                                  # still on its way
        status.respond(200, json={"result": {"post_id": "p9", "status": "published",
                                             "platforms": [{"platformPostUrl": "https://x.com/desk/status/9"}]}})
        assert asyncio.run(a.settle_posts()) == 1
        asyncio.run(a.publish_post(out["id"]))
    p = a.store.get_post(out["id"])
    assert p["status"] == "published" and p["url"].endswith("/9") and sent.call_count == 1
    assert b'"post_id":"p9"' in status.calls[0].request.content.replace(b" ", b"")


def test_a_post_never_confirmed_is_left_for_the_owner(tmp_path):
    import time
    a = agent_with(tmp_path, [fields()])
    out = asyncio.run(a.desk_post())
    with respx.mock:
        respx.post(ORBIO_POST).respond(202, json={"status": "running"})            # no post id to check
        asyncio.run(a.publish_post(out["id"]))
    assert a.store.get_post(out["id"])["status"] == "unknown"
    (tmp_path / "b").mkdir()
    b = agent_with(tmp_path / "b", [fields()])
    out = asyncio.run(b.desk_post())
    with respx.mock:
        respx.post(ORBIO_POST).respond(202, json={"status": "running", "result": {"post_id": "p2"}})
        asyncio.run(b.publish_post(out["id"]))
    assert asyncio.run(b.settle_posts(now=time.time() + 2 * 86400)) == 1
    assert b.store.get_post(out["id"])["status"] == "unknown"


def test_a_post_left_publishing_by_a_crash_is_left_for_the_owner(tmp_path):
    import time
    a = agent_with(tmp_path, [fields()])
    out = asyncio.run(a.desk_post())
    assert a.store.claim_post(out["id"], ("draft",), "publishing", ts=time.time())     # then the process died
    assert asyncio.run(a.settle_posts()) == 0                                         # within the hour: left alone
    assert asyncio.run(a.settle_posts(now=time.time() + 7200)) == 1
    assert a.store.get_post(out["id"])["status"] == "unknown"
    with respx.mock:
        route = respx.post(ORBIO_POST).respond(200, json=PUBLISHED)
        asyncio.run(a.publish_post(out["id"]))
    assert route.call_count == 0
