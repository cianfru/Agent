import datetime as dt

from synth import brief, detectors
from synth.sources.reflex import _span_h

COWBOY = {"coins": {
    "BTC": {"wallets_7d": {"state": "up", "net_delta_usd": 3.4e6}, "taker_net_24h_usd": {"call": 6.3e6, "put": 3.7e6}},
    "ETH": {"wallets_7d": {"state": None}, "wallets_30d": {"state": "defensive", "net_delta_usd": -8e5},
            "taker_net_24h_usd": {"call": 1e5, "put": 9e5}},
    "SOL": {"wallets_7d": {"state": "up"}},
}}
NOW = 1_791_600_000
REFLEX = {
    "convergences": [
        {"coin": "STRK", "side": "short", "n": 4, "span_h": 3, "ts": NOW - 3600},
        {"coin": "MET", "side": "short", "n": 2, "span_h": 20, "ts": NOW - 7200},
        {"coin": "OLD", "side": "long", "n": 5, "span_h": 1, "ts": NOW - 2 * 86400},
    ],
    "coins": {
        "PUMP": {"opens_24h": {"long": 7, "short": 1}},
        "ETH": {"opens_24h": {"long": 2, "short": 0}},
        "STRK": {"opens_24h": {"long": 0, "short": 5}},
    },
}


def test_derive_lines_btc_eth_hype_only():
    lines = brief.derive_lines(COWBOY)
    assert [d["coin"] for d in lines] == ["BTC", "ETH"]
    assert lines[0]["text"] == "BTC long · wallets +$3.4M 7d · calls bought"
    assert lines[1]["text"] == "ETH short · wallets -$800K 30d · puts bought"


def test_hl_today_fast_convergence_first_and_stale_dropped():
    rows = brief.hl_today(REFLEX, fast_hours=6, now=NOW)
    assert [r["coin"] for r in rows] == ["STRK", "MET", "PUMP"]
    assert rows[0]["text"] == "STRK short · 4 traders converged in 3h" and rows[0]["fast"]
    assert not rows[1]["fast"]
    assert rows[2]["text"] == "PUMP long · 7 new longs / 1 shorts today"


def test_format_brief_is_short():
    g = {"id": 7, "coin": "STRK", "side": "short", "venue": "hyperliquid"}
    members = [{"status": "open", "entry_px": 0.0712, "size_usd": 1, "last_px": 0.0732},
               {"status": "closed", "entry_px": 0.0743, "size_usd": 1, "last_px": 0.0732}]
    body = {"derive": brief.derive_lines(COWBOY), "hyperliquid": brief.hl_today(REFLEX, now=NOW),
            "groups": [{"id": 7, "text": brief.group_line(g, members)}]}
    text = brief.format_brief(body, dt.date(2026, 10, 10))
    assert text.splitlines()[0] == "Outrider · 10 Oct"
    assert ("#7 STRK short · 1/2 still in · entries 0.0712–0.0743 · now 0.0732 · all losing above 0.0743"
            in text)
    assert len(text.splitlines()) <= 15
    assert "None open" in brief.format_brief({})


def test_slow_convergence_logged_below_alert_bar():
    evs = detectors.reflex_feeds({"convergences": [
        {"coin": "STRK", "side": "short", "n": 4, "span_h": 3, "key": "a"},
        {"coin": "MET", "side": "short", "n": 2, "span_h": 20, "key": "b"},
    ]}, fast_hours=6)
    fast, slow = evs
    assert fast["score"] >= 80 and "in 3h" in fast["title"]
    assert slow["score"] < 60 and slow["data"]["fast"] is False


def test_span_parsed_from_reflex_detail():
    assert _span_h("4 profitable traders opened short within 3h at 0.0587 to 0.0743") == 3.0
    assert _span_h("no span here") is None
