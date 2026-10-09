from synth import detectors


def holder(addr, delta, rank=5, cohort="Money Printer", smart=True):
    return {"address": addr, "rank": rank, "cohort": cohort, "smart": smart, "net_delta_usd": delta}


def test_two_smart_wallets_moving_long_is_convergence():
    prev = {"wallets": {"HYPE": [holder("0xa", 0), holder("0xb", 50_000)]}}
    cur = {"wallets": {"HYPE": [holder("0xa", 400_000), holder("0xb", 600_000)]}}
    evs = detectors.wallet_moves("cowboy", prev, cur, min_usd=100_000)
    kinds = [e["kind"] for e in evs]
    assert kinds.count("wallet_move") == 2
    conv = [e for e in evs if e["kind"] == "smart_convergence"]
    assert len(conv) == 1
    assert conv[0]["coin"] == "HYPE" and conv[0]["direction"] == "long"
    assert conv[0]["score"] >= 70
    assert conv[0]["data"]["total_delta_usd"] == 950_000


def test_small_and_non_smart_moves_are_ignored():
    prev = {"wallets": {"BTC": [holder("0xa", 0), holder("0xb", 0, smart=False)]}}
    cur = {"wallets": {"BTC": [holder("0xa", 50_000), holder("0xb", 5_000_000, smart=False)]}}
    assert detectors.wallet_moves("cowboy", prev, cur, min_usd=100_000) == []


def test_wallet_that_leaves_holder_list_counts_as_close():
    prev = {"wallets": {"ETH": [holder("0xa", 300_000)]}}
    cur = {"wallets": {"ETH": []}}
    (ev,) = detectors.wallet_moves("cowboy", prev, cur, min_usd=100_000)
    assert ev["direction"] == "short" and "closed" in ev["title"]


def test_no_baseline_for_coin_means_no_events():
    cur = {"wallets": {"SOL": [holder("0xa", 900_000)]}}
    assert detectors.wallet_moves("cowboy", {"wallets": {}}, cur, min_usd=1) == []


def test_signal_change_to_strong_long():
    prev = {"coins": {"BTC": {"signal_1d": "WAIT", "regime_1d": "MARKUP"}}}
    cur = {"coins": {"BTC": {"signal_1d": "STRONG_LONG", "regime_1d": "MARKUP"}}}
    (ev,) = detectors.engine_changes("reflex", prev, cur)
    assert ev["kind"] == "signal_change" and ev["direction"] == "long" and ev["score"] == 75


def test_wait_transition_is_not_an_event_but_regime_change_is():
    prev = {"coins": {"ETH": {"signal_1d": "STRONG_LONG", "regime_1d": "MARKUP"}}}
    cur = {"coins": {"ETH": {"signal_1d": "WAIT", "regime_1d": "BLOWOFF"}}}
    evs = detectors.engine_changes("cowboy", prev, cur)
    assert [e["kind"] for e in evs] == ["regime_change"]
    assert evs[0]["direction"] == "short"


def test_reflex_convergence_feed_scores_high():
    cur = {"convergences": [{"coin": "PUMP", "side": "long", "n": 3, "key": "conv:PUMP:long:1:3"}]}
    (ev,) = detectors.reflex_feeds(cur)
    assert ev["kind"] == "smart_convergence" and ev["score"] == 85
    assert ev["key"] == "reflex:conv:PUMP:long:1:3"


def test_crowded_market():
    cur = {"market": {"consensus_1d": "RISK-ON", "consensus_1d_strength": 90, "fear_greed": 78},
           "coins": {"A": {"heat_1d": 85}, "B": {"heat_1d": 20}}}
    (ev,) = detectors.crowded("cowboy", cur)
    assert ev["kind"] == "crowded" and ev["data"]["hot"] == ["A"]


def test_cross_source_confirmation_from_standing_lean():
    events = detectors.reflex_feeds({"convergences": [{"coin": "BTC", "side": "long", "n": 2}]})
    snaps = {"reflex": {}, "cowboy": {"coins": {"BTC": {"wallets_30d": {"state": "up"}}}}}
    (ev,) = detectors.cross_source(events, snaps)
    assert ev["kind"] == "cross_confirm" and ev["coin"] == "BTC" and ev["score"] == 55  # dashboard, not a ping


def test_cross_source_both_fired():
    events = [
        {"kind": "smart_convergence", "coin": "SOL", "direction": "short", "data": {"source": "reflex"}},
        {"kind": "wallet_move", "coin": "SOL", "direction": "short", "data": {"source": "cowboy"}},
    ]
    (ev,) = detectors.cross_source(events, {})
    assert ev["score"] == 80 and ev["data"]["sources"] == ["cowboy", "reflex"]


def test_engine_events_never_start_a_cross_confirm():
    events = [
        {"kind": "signal_change", "coin": "PUMP", "direction": "long", "data": {"source": "cowboy"}},
        {"kind": "signal_change", "coin": "PUMP", "direction": "long", "data": {"source": "reflex"}},
    ]
    snaps = {"reflex": {"coins": {"PUMP": {"smart_lean": "long"}}}, "cowboy": {}}
    assert detectors.cross_source(events, snaps) == []


def test_lean_flip():
    prev = {"coins": {"ETH": {"smart_lean": "long"}}}
    cur = {"coins": {"ETH": {"smart_lean": "short", "smart_short": {"n": 4}}}}
    (ev,) = detectors.lean_flips("reflex", prev, cur)
    assert ev["direction"] == "short"
