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
            "SELECT id, ts, coin, direction, entry_price, ret_1d, ret_3d, ret_7d FROM picks "
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

    # briefs -------------------------------------------------------------
    def add_brief(self, kind: str, body: dict, usage: dict | None = None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO briefs(ts, kind, body, usage) VALUES(?,?,?,?)",
                (time.time(), kind, json.dumps(body), json.dumps(usage or {})),
            )
            self._db.commit()
            return cur.lastrowid

    def latest_brief(self, kind: str) -> dict | None:
        row = self._db.execute(
            "SELECT * FROM briefs WHERE kind=? ORDER BY ts DESC LIMIT 1", (kind,)
        ).fetchone()
        if not row:
            return None
        return {"ts": row["ts"], "kind": row["kind"], **json.loads(row["body"])}


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
