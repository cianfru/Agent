"""SQLite persistence: last snapshot per source, detected events, LLM briefs."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    source TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    key TEXT NOT NULL,
    kind TEXT NOT NULL,
    coin TEXT,
    direction TEXT,
    score REAL NOT NULL,
    title TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS events_key ON events(key, ts);
CREATE TABLE IF NOT EXISTS picks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    origin TEXT NOT NULL,
    coin TEXT NOT NULL,
    direction TEXT NOT NULL,
    conviction INTEGER,
    entry_price REAL,
    thesis TEXT,
    state TEXT NOT NULL,
    ret_1d REAL,
    ret_3d REAL,
    ret_7d REAL
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL,
    pick_id INTEGER,
    orbio_post_id TEXT,
    url TEXT,
    error TEXT,
    published_ts REAL
);
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    venue TEXT NOT NULL,
    coin TEXT NOT NULL,
    side TEXT NOT NULL,
    key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'open',
    followed INTEGER NOT NULL DEFAULT 0,
    alerted INTEGER NOT NULL DEFAULT 0,
    ref_px REAL,
    closed_ts REAL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    address TEXT NOT NULL,
    instrument TEXT,
    side TEXT NOT NULL,
    entry_px REAL,
    size REAL,
    size_usd REAL,
    opened_ts REAL NOT NULL,
    expiry REAL,
    status TEXT NOT NULL DEFAULT 'open',
    exit_px REAL,
    exit_ts REAL,
    pnl_pct REAL,
    pnl_usd REAL,
    last_px REAL,
    last_seen_ts REAL,
    approx INTEGER NOT NULL DEFAULT 0,
    UNIQUE(group_id, address, instrument)
);
CREATE INDEX IF NOT EXISTS members_group ON members(group_id);
CREATE TABLE IF NOT EXISTS pt_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    coin TEXT NOT NULL,
    side TEXT NOT NULL,
    opened_ts REAL NOT NULL,
    known_open INTEGER NOT NULL DEFAULT 1,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    value REAL,
    max_value REAL,
    entry_px REAL,
    upnl REAL,
    status TEXT NOT NULL DEFAULT 'open',
    closed_ts REAL,
    baseline INTEGER NOT NULL DEFAULT 0,
    conviction INTEGER NOT NULL DEFAULT 0,
    pick_id INTEGER,
    size REAL
);
CREATE TABLE IF NOT EXISTS pt_flow (
    ts REAL NOT NULL,
    address TEXT NOT NULL,
    coin TEXT NOT NULL,
    side TEXT NOT NULL,
    usd REAL NOT NULL,
    px REAL
);
CREATE INDEX IF NOT EXISTS pt_flow_ts ON pt_flow(ts);
CREATE TABLE IF NOT EXISTS pt_list (
    address TEXT PRIMARY KEY,
    info TEXT NOT NULL,
    added_ts REAL NOT NULL,
    qualified_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS pt_positions_open ON pt_positions(address, status);
CREATE TABLE IF NOT EXISTS pt_cand (
    address TEXT PRIMARY KEY,
    src TEXT,
    info TEXT,
    seen_ts REAL,
    checked_ts REAL,
    status TEXT,
    fills_ms INTEGER,
    state TEXT,
    closed TEXT,
    moves TEXT,
    moves_last TEXT,
    record TEXT,
    subs TEXT,
    subs_ts REAL
);
CREATE TABLE IF NOT EXISTS pt_traders (
    address TEXT PRIMARY KEY,
    equity REAL,
    gross REAL,
    last_seen REAL
);
CREATE TABLE IF NOT EXISTS series (
    ts REAL NOT NULL,
    source TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS series_src_ts ON series(source, ts);
CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    body TEXT NOT NULL,
    usage TEXT
);
"""


class Store:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(picks)")}
            for col in ("ret_14d", "ret_30d"):   # added with the position traders' 14/30-day scoring
                if col not in cols:
                    self._db.execute(f"ALTER TABLE picks ADD COLUMN {col} REAL")
            if "size" not in {r[1] for r in self._db.execute("PRAGMA table_info(pt_positions)")}:
                self._db.execute("ALTER TABLE pt_positions ADD COLUMN size REAL")
            post_cols = {r[1] for r in self._db.execute("PRAGMA table_info(posts)")}
            for col, typ in (("story", "TEXT"), ("tg_msg", "INTEGER")):   # Outrider's posts (synth/persona.py)
                if col not in post_cols:
                    self._db.execute(f"ALTER TABLE posts ADD COLUMN {col} {typ}")
            self._db.commit()

    # snapshots ----------------------------------------------------------
    def get_snapshot(self, source: str) -> dict | None:
        row = self._db.execute("SELECT body FROM snapshots WHERE source=?", (source,)).fetchone()
        return json.loads(row["body"]) if row else None

    def put_snapshot(self, source: str, body: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO snapshots(source, ts, body) VALUES(?,?,?) "
                "ON CONFLICT(source) DO UPDATE SET ts=excluded.ts, body=excluded.body",
                (source, time.time(), json.dumps(body)),
            )
            self._db.commit()

    # series: one compact row per poll, for the live flow --------------------
    def add_series(self, source: str, body: dict, ts: float | None = None) -> None:
        with self._lock:
            self._db.execute("INSERT INTO series(ts, source, body) VALUES(?,?,?)",
                             (ts or time.time(), source, json.dumps(body, separators=(",", ":"))))
            self._db.commit()

    def series(self, source: str, since: float) -> list[tuple[float, dict]]:
        rows = self._db.execute("SELECT ts, body FROM series WHERE source=? AND ts>=? ORDER BY ts",
                                (source, since)).fetchall()
        return [(r["ts"], json.loads(r["body"])) for r in rows]

    def prune_series(self, before: float) -> None:
        with self._lock:
            self._db.execute("DELETE FROM series WHERE ts<?", (before,))
            self._db.commit()

    # events -------------------------------------------------------------
    def seen_recently(self, key: str, within_s: float) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM events WHERE key=? AND ts>=? LIMIT 1", (key, time.time() - within_s)
        ).fetchone()
        return row is not None

    def add_event(self, ev: dict) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events(ts, key, kind, coin, direction, score, title, data) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    ev.get("ts", time.time()),
                    ev["key"],
                    ev["kind"],
                    ev.get("coin"),
                    ev.get("direction"),
                    ev["score"],
                    ev["title"],
                    json.dumps(ev.get("data", {})),
                ),
            )
            self._db.commit()
            return cur.lastrowid

    def event(self, event_id: int | None) -> dict | None:
        if event_id is None:
            return None
        row = self._db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return _event(row) if row else None

    def events(self, since: float = 0, coin: str | None = None, min_score: float = 0,
               limit: int = 100) -> list[dict]:
        q = "SELECT * FROM events WHERE ts>=? AND score>=?"
        args: list[Any] = [since, min_score]
        if coin:
            q += " AND coin=?"
            args.append(coin.upper())
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        return [_event(r) for r in self._db.execute(q, args).fetchall()]

    # picks journal: logged before the outcome, scored at 1/3/7 days --------
    def add_pick(self, origin: str, coin: str, direction: str, conviction: int | None,
                 entry_price: float | None, thesis: str, state: dict) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO picks(ts, origin, coin, direction, conviction, entry_price, thesis, state) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (time.time(), origin, coin.upper(), direction, conviction, entry_price, thesis,
                 json.dumps(state)),
            )
            self._db.commit()
            return cur.lastrowid

    def open_picks(self, now: float | None = None) -> list[dict]:
        rows = self._db.execute(
            "SELECT id, ts, origin, coin, direction, entry_price, ret_1d, ret_3d, ret_7d, ret_14d, ret_30d FROM picks "
            "WHERE entry_price IS NOT NULL AND (ret_7d IS NULL OR (origin='position' AND ret_30d IS NULL AND ts > ?))",
            ((now or time.time()) - 32 * 86400,),
        ).fetchall()
        return [dict(r) for r in rows]

    def set_pick_return(self, pick_id: int, horizon: str, ret: float) -> None:
        assert horizon in ("1d", "3d", "7d", "14d", "30d")
        with self._lock:
            self._db.execute(f"UPDATE picks SET ret_{horizon}=? WHERE id=?", (ret, pick_id))
            self._db.commit()

    def picks(self, limit: int = 100, origin: str | None = None, since: float = 0) -> list[dict]:
        q = ("SELECT id, ts, origin, coin, direction, conviction, entry_price, thesis, ret_1d, ret_3d, ret_7d, "
             "ret_14d, ret_30d "
             "FROM picks WHERE ts>=?")
        args: list[Any] = [since]
        if origin:
            q += " AND origin=?"
            args.append(origin)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self._db.execute(q, args).fetchall()]

    def set_pick_conviction(self, pick_id: int, conviction: int) -> None:
        with self._lock:
            self._db.execute("UPDATE picks SET conviction=? WHERE id=?", (conviction, pick_id))
            self._db.commit()

    # proven position traders (synth/position_traders.py) ----------------------
    def pt_trader(self, address: str) -> dict | None:
        row = self._db.execute("SELECT * FROM pt_traders WHERE address=?", (address,)).fetchone()
        return dict(row) if row else None

    def pt_set_trader(self, address: str, equity: float, gross: float, ts: float) -> None:
        with self._lock:
            self._db.execute("INSERT INTO pt_traders(address, equity, gross, last_seen) VALUES(?,?,?,?) "
                             "ON CONFLICT(address) DO UPDATE SET equity=excluded.equity, gross=excluded.gross, "
                             "last_seen=excluded.last_seen", (address, equity, gross, ts))
            self._db.commit()

    def pt_open(self, address: str) -> list[dict]:
        rows = self._db.execute("SELECT * FROM pt_positions WHERE address=? AND status='open'", (address,)).fetchall()
        return [dict(r) for r in rows]

    def pt_open_position(self, address: str, coin: str, side: str, opened_ts: float, known_open: int, now: float,
                         value: float, entry_px: float, upnl: float, baseline: int, pick_id: int | None,
                         size: float | None = None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO pt_positions(address, coin, side, opened_ts, known_open, first_seen, last_seen, value, "
                "max_value, entry_px, upnl, baseline, pick_id, size) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (address, coin, side, opened_ts, known_open, now, now, value, value, entry_px, upnl, baseline, pick_id,
                 size))
            self._db.commit()
            return cur.lastrowid

    def pt_touch(self, row_id: int, value: float, entry_px: float, upnl: float, now: float,
                 size: float | None = None) -> None:
        with self._lock:
            self._db.execute("UPDATE pt_positions SET value=?, max_value=MAX(COALESCE(max_value, 0), ?), entry_px=?, "
                             "upnl=?, last_seen=?, size=COALESCE(?, size) WHERE id=?",
                             (value, value, entry_px, upnl, now, size, row_id))
            self._db.commit()

    def pt_add_flow(self, ts: float, address: str, coin: str, side: str, usd: float, px: float | None) -> None:
        with self._lock:
            self._db.execute("INSERT INTO pt_flow(ts, address, coin, side, usd, px) VALUES(?,?,?,?,?,?)",
                             (ts, address, coin, side, usd, px))
            self._db.execute("DELETE FROM pt_flow WHERE ts<?", (ts - 35 * 86400,))
            self._db.commit()

    def pt_flow_since(self, since: float) -> list[dict]:
        rows = self._db.execute("SELECT * FROM pt_flow WHERE ts>=? ORDER BY ts", (since,)).fetchall()
        return [dict(r) for r in rows]

    def pt_list(self) -> dict[str, dict]:
        return {r["address"]: json.loads(r["info"]) for r in self._db.execute("SELECT * FROM pt_list").fetchall()}

    def pt_list_upsert(self, address: str, info: dict, now: float, added_ts: float | None = None,
                       qualified_ts: float | None = None) -> None:
        with self._lock:
            self._db.execute("INSERT INTO pt_list(address, info, added_ts, qualified_ts) VALUES(?,?,?,?) "
                             "ON CONFLICT(address) DO UPDATE SET info=excluded.info, qualified_ts=excluded.qualified_ts",
                             (address, json.dumps(info), added_ts if added_ts is not None else now,
                              qualified_ts if qualified_ts is not None else now))
            self._db.commit()

    def pt_list_rows(self) -> list[dict]:
        return [{"address": r["address"], "info": json.loads(r["info"]), "added_ts": r["added_ts"],
                 "qualified_ts": r["qualified_ts"]} for r in self._db.execute("SELECT * FROM pt_list").fetchall()]

    def pt_list_prune(self, before: dict[str, float]) -> list[str]:
        """Drop listed traders whose last qualifying check is older than their tier's cut-off; returns who dropped."""
        gone = [r["address"] for r in self.pt_list_rows()
                if r["qualified_ts"] < before.get(r["info"].get("tier", "riding"), float("inf"))]
        with self._lock:
            self._db.executemany("DELETE FROM pt_list WHERE address=?", [(a,) for a in gone])
            self._db.commit()
        return gone

    # proven-list candidates (synth/proven.py) ------------------------------------
    _CAND_JSON = ("src", "info", "state", "closed", "moves", "moves_last", "record", "subs")

    def pt_cand_pool(self, pool: dict[str, tuple[list[str], dict]], now: float) -> None:
        """Add or refresh candidates: their sources and leaderboard info (kept when none is given)."""
        with self._lock:
            for a, (src, info) in pool.items():
                self._db.execute(
                    "INSERT INTO pt_cand(address, src, info, seen_ts) VALUES(?,?,?,?) ON CONFLICT(address) DO UPDATE "
                    "SET src=excluded.src, seen_ts=excluded.seen_ts, "
                    "info=CASE WHEN excluded.info='{}' THEN pt_cand.info ELSE excluded.info END",
                    (a, json.dumps(src), json.dumps(info or {}), now))
            self._db.commit()

    def pt_cand_prune(self, before: float, keep: set[str]) -> int:
        old = [r[0] for r in self._db.execute("SELECT address FROM pt_cand WHERE seen_ts<?", (before,)).fetchall()
               if r[0] not in keep]
        with self._lock:
            self._db.executemany("DELETE FROM pt_cand WHERE address=?", [(a,) for a in old])
            self._db.commit()
        return len(old)

    def pt_cand_rows(self) -> list[dict]:
        rows = self._db.execute("SELECT address, src, info, seen_ts, checked_ts, status FROM pt_cand").fetchall()
        return [{"address": r["address"], "src": json.loads(r["src"] or "[]"), "info": json.loads(r["info"] or "{}"),
                 "seen_ts": r["seen_ts"], "checked_ts": r["checked_ts"], "status": r["status"]} for r in rows]

    def pt_cand_get(self, address: str) -> dict | None:
        r = self._db.execute("SELECT * FROM pt_cand WHERE address=?", (address,)).fetchone()
        if not r:
            return None
        return {k: (json.loads(r[k]) if r[k] is not None else None) if k in self._CAND_JSON else r[k] for k in r.keys()}

    def pt_cand_save(self, address: str, **fields: Any) -> None:
        cols = [k for k in fields if k in self._CAND_JSON or k in ("checked_ts", "status", "fills_ms", "subs_ts",
                                                                 "seen_ts")]
        vals = [json.dumps(fields[k]) if k in self._CAND_JSON and fields[k] is not None else fields[k] for k in cols]
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO pt_cand(address) VALUES(?)", (address,))
            self._db.execute(f"UPDATE pt_cand SET {', '.join(c + '=?' for c in cols)} WHERE address=?", (*vals, address))
            self._db.commit()

    def pt_cand_checked(self) -> set[str]:
        return {r[0] for r in self._db.execute("SELECT address FROM pt_cand WHERE checked_ts IS NOT NULL").fetchall()}

    def pt_cand_moves(self) -> dict[str, list]:
        return {r[0]: json.loads(r[1]) for r in self._db.execute(
            "SELECT address, moves FROM pt_cand WHERE moves IS NOT NULL AND moves != '[]'").fetchall()}

    def pt_cand_subs(self) -> dict[str, list]:
        return {r[0]: json.loads(r[1]) for r in self._db.execute(
            "SELECT address, subs FROM pt_cand WHERE subs IS NOT NULL AND subs != '[]'").fetchall()}

    def pt_close(self, row_id: int, now: float) -> None:
        with self._lock:
            self._db.execute("UPDATE pt_positions SET status='closed', closed_ts=? WHERE id=?", (now, row_id))
            self._db.commit()

    def pt_mark_conviction(self, row_id: int) -> None:
        with self._lock:
            self._db.execute("UPDATE pt_positions SET conviction=1 WHERE id=?", (row_id,))
            self._db.commit()

    def delete_pick(self, pick_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM picks WHERE id=?", (pick_id,))
            self._db.commit()

    # public posts --------------------------------------------------------
    def add_post(self, kind: str, text: str, status: str, pick_id: int | None = None,
                 error: str | None = None, story: dict | None = None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO posts(ts, kind, text, status, pick_id, error, story) VALUES(?,?,?,?,?,?,?)",
                (time.time(), kind, text, status, pick_id, error, json.dumps(story) if story else None),
            )
            self._db.commit()
            return cur.lastrowid

    def update_post(self, pid: int, **fields: Any) -> None:
        allowed = {"status", "orbio_post_id", "url", "error", "published_ts", "text", "story", "tg_msg"}
        cols = {k: (json.dumps(v) if k == "story" and v is not None else v) for k, v in fields.items() if k in allowed}
        if not cols:
            return
        with self._lock:
            self._db.execute(
                f"UPDATE posts SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", (*cols.values(), pid)
            )
            self._db.commit()

    @staticmethod
    def _post_row(row) -> dict:
        d = dict(row)
        d["story"] = json.loads(d["story"]) if d.get("story") else None
        return d

    def get_post(self, post_id: int) -> dict | None:
        row = self._db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        return self._post_row(row) if row else None

    def post_by_tg(self, message_id: int) -> dict | None:
        row = self._db.execute("SELECT * FROM posts WHERE tg_msg=? ORDER BY id DESC LIMIT 1", (message_id,)).fetchone()
        return self._post_row(row) if row else None

    def posts(self, limit: int = 50, kind: str | None = None, since: float = 0) -> list[dict]:
        q, args = "SELECT * FROM posts WHERE ts>=?", [since]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        return [self._post_row(r) for r in self._db.execute(q, args).fetchall()]

    # groups: traders who converged, tracked to their close ------------------
    def add_group(self, venue: str, coin: str, side: str, key: str, ref_px: float | None,
                  meta: dict | None = None, ts: float | None = None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO groups(ts, venue, coin, side, key, ref_px, meta) VALUES(?,?,?,?,?,?,?)",
                (ts or time.time(), venue, coin.upper(), side, key, ref_px, json.dumps(meta or {})),
            )
            self._db.commit()
            return cur.lastrowid

    def group_by_key(self, key: str) -> dict | None:
        row = self._db.execute("SELECT * FROM groups WHERE key=?", (key,)).fetchone()
        return _group(row) if row else None

    def get_group(self, gid: int) -> dict | None:
        row = self._db.execute("SELECT * FROM groups WHERE id=?", (gid,)).fetchone()
        return _group(row) if row else None

    def groups(self, status: str | None = None, limit: int = 200) -> list[dict]:
        q, args = "SELECT * FROM groups", []
        if status:
            q += " WHERE status=?"
            args.append(status)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        return [_group(r) for r in self._db.execute(q, args).fetchall()]

    def update_group(self, gid: int, **fields: Any) -> None:
        allowed = {"status", "followed", "alerted", "closed_ts", "meta"}
        cols = {k: (json.dumps(v) if k == "meta" else v) for k, v in fields.items() if k in allowed}
        if cols:
            with self._lock:
                self._db.execute(f"UPDATE groups SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?",
                                 (*cols.values(), gid))
                self._db.commit()

    def delete_group(self, gid: int) -> None:
        """Remove a group, its members and its scoring pick (used to purge false groups)."""
        with self._lock:
            self._db.execute("DELETE FROM members WHERE group_id=?", (gid,))
            self._db.execute("DELETE FROM groups WHERE id=?", (gid,))
            self._db.execute("DELETE FROM picks WHERE origin='group' AND thesis=?", (f"group #{gid}",))
            self._db.commit()

    def add_member(self, gid: int, m: dict) -> int | None:
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO members(group_id, address, instrument, side, entry_px, size, size_usd, "
                "opened_ts, expiry, last_px, last_seen_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (gid, m["address"].lower(), m.get("instrument") or "", m["side"], m.get("entry_px"), m.get("size"),
                 m.get("size_usd"), m["opened_ts"], m.get("expiry"), m.get("entry_px"), m["opened_ts"]),
            )
            self._db.commit()
            return cur.lastrowid if cur.rowcount else None

    def members(self, gid: int) -> list[dict]:
        rows = self._db.execute("SELECT * FROM members WHERE group_id=? ORDER BY opened_ts", (gid,)).fetchall()
        return [dict(r) for r in rows]

    def update_member(self, mid: int, **fields: Any) -> None:
        allowed = {"status", "exit_px", "exit_ts", "pnl_pct", "pnl_usd", "last_px", "last_seen_ts", "approx", "size"}
        cols = {k: v for k, v in fields.items() if k in allowed}
        if cols:
            with self._lock:
                self._db.execute(f"UPDATE members SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?",
                                 (*cols.values(), mid))
                self._db.commit()

    # briefs -------------------------------------------------------------
    def add_brief(self, kind: str, body: dict, usage: dict | None = None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO briefs(ts, kind, body, usage) VALUES(?,?,?,?)",
                (time.time(), kind, json.dumps(body), json.dumps(usage or {})),
            )
            self._db.commit()
            return cur.lastrowid

    def count_briefs(self, kind: str, since: float) -> int:
        return self._db.execute("SELECT COUNT(*) FROM briefs WHERE kind=? AND ts>=?", (kind, since)).fetchone()[0]

    def briefs_since(self, kind: str, since: float) -> list[dict]:
        rows = self._db.execute("SELECT ts, body FROM briefs WHERE kind=? AND ts>=? ORDER BY ts", (kind, since)).fetchall()
        return [{"ts": r["ts"], "body": json.loads(r["body"])} for r in rows]

    def latest_brief(self, kind: str) -> dict | None:
        row = self._db.execute(
            "SELECT * FROM briefs WHERE kind=? ORDER BY ts DESC LIMIT 1", (kind,)
        ).fetchone()
        if not row:
            return None
        return {"ts": row["ts"], "kind": row["kind"], **json.loads(row["body"])}


def _group(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["meta"] = json.loads(d.get("meta") or "{}")
    d["followed"] = bool(d["followed"])
    d["alerted"] = bool(d["alerted"])
    return d


def _event(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "ts": r["ts"],
        "kind": r["kind"],
        "coin": r["coin"],
        "direction": r["direction"],
        "score": r["score"],
        "title": r["title"],
        "data": json.loads(r["data"]),
    }
