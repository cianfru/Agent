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

    def open_picks(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT id, ts, origin, coin, direction, entry_price, ret_1d, ret_3d, ret_7d FROM picks "
            "WHERE ret_7d IS NULL AND entry_price IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]

    def set_pick_return(self, pick_id: int, horizon: str, ret: float) -> None:
        assert horizon in ("1d", "3d", "7d")
        with self._lock:
            self._db.execute(f"UPDATE picks SET ret_{horizon}=? WHERE id=?", (ret, pick_id))
            self._db.commit()

    def picks(self, limit: int = 100, origin: str | None = None, since: float = 0) -> list[dict]:
        q = ("SELECT id, ts, origin, coin, direction, conviction, entry_price, thesis, ret_1d, ret_3d, ret_7d "
             "FROM picks WHERE ts>=?")
        args: list[Any] = [since]
        if origin:
            q += " AND origin=?"
            args.append(origin)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self._db.execute(q, args).fetchall()]

    def delete_pick(self, pick_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM picks WHERE id=?", (pick_id,))
            self._db.commit()

    # public posts --------------------------------------------------------
    def add_post(self, kind: str, text: str, status: str, pick_id: int | None = None,
                 error: str | None = None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO posts(ts, kind, text, status, pick_id, error) VALUES(?,?,?,?,?,?)",
                (time.time(), kind, text, status, pick_id, error),
            )
            self._db.commit()
            return cur.lastrowid

    def update_post(self, pid: int, **fields: Any) -> None:
        allowed = {"status", "orbio_post_id", "url", "error", "published_ts", "text"}
        cols = {k: v for k, v in fields.items() if k in allowed}
        if not cols:
            return
        with self._lock:
            self._db.execute(
                f"UPDATE posts SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", (*cols.values(), pid)
            )
            self._db.commit()

    def get_post(self, post_id: int) -> dict | None:
        row = self._db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        return dict(row) if row else None

    def posts(self, limit: int = 50, kind: str | None = None, since: float = 0) -> list[dict]:
        q, args = "SELECT * FROM posts WHERE ts>=?", [since]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self._db.execute(q, args).fetchall()]

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
