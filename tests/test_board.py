from synth import board

SNAPS = {
    "cowboy": {"coins": {
        "ETH": {"price": 2500, "unified": "LIGHT_LONG", "wallets_30d": {"state": "up", "net_delta_usd": 8e5},
                "options_7d": {"state": "defensive"}},
        "BTC": {"price": 82000, "unified": "WAIT", "wallets_30d": {"state": "neutral"}},
    }},
    "reflex": {"coins": {
        "ETH": {"price": 2501, "smart_lean": "long", "smart_long": {"n": 7, "usd": 2e6}, "smart_short": {"n": 2, "usd": 3e5}},
        "PUMP": {"price": 0.005, "signal_1d": "STRONG_LONG", "smart_lean": "long",
                 "convergence": {"side": "long", "n": 3, "ts": 1}},
    }},
}


def test_board_votes_and_order():
    events = [{"coin": "PUMP", "score": 85, "title": "3 converged long on PUMP"}]
    rows = board.smart_board(SNAPS, events)
    eth = next(r for r in rows if r["coin"] == "ETH")
    assert eth["votes"] == {"engine": "long", "hl_smart": "long", "derive": "long", "options": "short"}
    assert (eth["longs"], eth["shorts"], eth["agree"], eth["layers"]) == (3, 1, 2, 4)
    assert eth["hl"]["long_n"] == 7 and eth["derive"]["net_30d"] == 8e5
    assert [r["coin"] for r in rows] == ["PUMP", "ETH", "BTC"]  # agree 2 with an 85 event, agree 2, agree 0
    pump = next(r for r in rows if r["coin"] == "PUMP")
    assert pump["top_event"] and pump["derive"] is None


def test_pulse_text():
    events = [
        {"coin": "PUMP", "score": 85, "title": "3 converged long on PUMP"},
        {"coin": None, "score": 60, "title": "Crowded market"},
        {"coin": "PUMP", "score": 40, "title": "PUMP OI surge"},
    ]
    rows = [{"coin": "ETH", "agree": 3, "layers": 4}]
    text = board.pulse_text(events, rows)
    assert text.splitlines()[0] == "Last hour"
    assert "Crowded market" in text and "3 converged long on PUMP (+1 more)" in text
    assert "Layers agreeing: ETH long 3/4" in text
    assert board.pulse_text([], rows) is None
