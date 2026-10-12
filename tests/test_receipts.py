import hashlib
import json
import sqlite3
import time

from synth import receipts
from synth.store import Store

WALLET = "0x" + "c0ffee" * 6 + "abcd"


def check_like_an_outsider(entries):
    """What anyone can run on /r/ledger.json, without our code."""
    prev = "0" * 64
    for e in entries:
        if e["prev"] != prev:
            return False
        if e["body"] is not None and hashlib.sha256(e["body"].encode()).hexdigest() != e["body_hash"]:
            return False
        if hashlib.sha256(f"{prev}:{e['body_hash']}".encode()).hexdigest() != e["hash"]:
            return False
        prev = e["hash"]
    return True


def journal(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    a = store.add_pick("alert", "HYPE", "long", 4, 40.0, "smart wallets converged", {"wallet": WALLET})
    b = store.add_pick("position", "SOL", "short", 1, 150.0, f"pt {WALLET} SOL short", {"wallet": WALLET})
    c = store.add_pick("post", "BTC", "long", None, 100_000.0, "setup", {})
    store.set_pick_return(a, "1d", 0.05)
    store.set_pick_return(a, "1d", 0.99)                  # written once: no second entry
    store.set_pick_return(b, "1d", -0.02)
    store.void_pick(c, "draft rejected")
    store.mark_pick_published(a, time.time(), 41.0)
    return store, (a, b, c)


def test_every_change_to_a_pick_is_a_link_in_one_chain(tmp_path):
    store, (a, b, c) = journal(tmp_path)
    kinds = [r["kind"] for r in store.ledger()]
    assert kinds == ["call", "call", "call", "score", "score", "void", "posted"]
    v = store.ledger_verify()
    assert v["ok"] and v["length"] == 7
    db = sqlite3.connect(str(tmp_path / "t.db"))
    db.execute("UPDATE ledger SET body=replace(body, '0.05', '0.50') WHERE kind='score' AND pick_id=?", (a,))
    db.commit()
    assert store.ledger_verify() == {"ok": False, "length": 4, "head": store.ledger()[2]["hash"], "broken_at": 4}


def test_the_public_sees_calls_after_the_delay_and_never_the_private_reasons(tmp_path):
    store, (a, b, c) = journal(tmp_path)
    now = time.time()
    early = receipts.public(store, 24 * 3600, now=now)
    assert early["calls"] == [] and early["withheld"] == 3
    led = receipts.public_ledger(store, 24 * 3600, now=now)["entries"]
    assert all(e["body"] is None for e in led) and check_like_an_outsider(led)
    later = receipts.public(store, 24 * 3600, now=now + 25 * 3600)
    assert later["withheld"] == 0 and later["chain"]["ok"] and later["chain"]["length"] == 7
    by_pick = {x["pick"]: x for x in later["calls"]}
    assert by_pick[a]["returns"] == {"1d": 0.05} and by_pick[a]["posted"]["px"] == 41.0
    assert by_pick[b]["source"] == "tracked trader" and by_pick[c]["void"] == "draft rejected"
    s = later["summary"]["all"]
    assert s["calls"] == 2 and s["void"] == 1 and s["horizons"]["1d"] == {"n": 2, "right_way": 1,
                                                                           "median_pct": 1.5, "mean_pct": 1.5}
    text = json.dumps(later) + json.dumps(receipts.public_ledger(store, 24 * 3600, now=now + 25 * 3600))
    assert WALLET not in text and "smart wallets converged" not in text      # committed by hash, not shown
    led = receipts.public_ledger(store, 24 * 3600, now=now + 25 * 3600)["entries"]
    assert all(e["body"] for e in led) and check_like_an_outsider(led)


def test_picks_from_before_the_ledger_join_it_marked_backfilled(tmp_path):
    path = str(tmp_path / "old.db")
    store = Store(path)
    store.add_pick("alert", "ETH", "long", 3, 2500.0, "t", {})
    db = sqlite3.connect(path)
    db.execute("DELETE FROM ledger")                       # as a database from before the ledger
    db.execute("UPDATE picks SET ret_1d=0.01, void_reason=NULL")
    db.commit()
    again = Store(path)
    rows = again.ledger()
    assert [r["kind"] for r in rows] == ["call", "score"]
    assert all(json.loads(r["body"]).get("backfilled") for r in rows) and again.ledger_verify()["ok"]
    assert len(Store(path).ledger()) == 2                  # once only


def test_public_routes_need_no_key(tmp_path, monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SYNTH_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("SYNTH_POLL_SECONDS", "0")
    monkeypatch.setenv("SYNTH_API_KEY", "k")
    monkeypatch.setenv("RECEIPTS_DELAY_H", "0")
    import synth.api
    mod = importlib.reload(synth.api)
    mod.agent.store.add_pick("alert", "HYPE", "long", 4, 40.0, "t", {"wallet": WALLET})
    c = TestClient(mod.app)
    r = c.get("/r/receipts.json")
    assert r.status_code == 200 and r.json()["calls"][0]["coin"] == "HYPE" and "max-age=60" in r.headers["cache-control"]
    assert check_like_an_outsider(c.get("/r/ledger.json").json()["entries"])
    page = c.get("/r")
    assert page.status_code == 200 and "Every call, scored." in page.text
    csp = page.headers["content-security-policy"]
    assert "script-src 'sha256-" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
