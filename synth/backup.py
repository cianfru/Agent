"""Backups. The journal (picks, posts, groups and their traders) is the one part of the database that cannot be read
again from Hyperliquid, Reflex or Cowboy, so it is exported once a day as gzipped JSON, the last 30 kept beside the
database. A consistent copy of the whole database is served on request (`GET /backup`) for keeping elsewhere.
Nothing is written when the disk is nearly full: a full volume would stop the database itself."""

from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import os
import shutil
import time

from .store import Store

log = logging.getLogger(__name__)

KEEP = 30
MIN_FREE_FRACTION = 0.10
MIN_FREE_BYTES = 50 * 1024 * 1024


def room(folder: str) -> bool:
    u = shutil.disk_usage(folder)
    return u.free >= MIN_FREE_BYTES and u.free >= MIN_FREE_FRACTION * u.total


def daily(store: Store, folder: str, now: float | None = None) -> str | None:
    """Today's journal export, unless it exists already or the disk is short. Returns the file written, else None."""
    now = now or time.time()
    os.makedirs(folder, exist_ok=True)
    name = f"journal-{dt.datetime.fromtimestamp(now, dt.timezone.utc):%Y-%m-%d}.json.gz"
    path = os.path.join(folder, name)
    if os.path.exists(path):
        return None
    if not room(folder):
        raise OSError(f"disk nearly full: journal backup {name} not written")
    tmp = path + ".part"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump({"at": now, **store.journal()}, f, separators=(",", ":"))
    os.replace(tmp, path)
    old = sorted(x for x in os.listdir(folder) if x.startswith("journal-") and x.endswith(".json.gz"))
    for x in old[:-KEEP]:
        os.remove(os.path.join(folder, x))
    log.info("journal backup %s", name)
    return path


def snapshot(store: Store, folder: str) -> str:
    """A consistent copy of the whole database in `folder` (the caller deletes it once sent)."""
    os.makedirs(folder, exist_ok=True)
    if not room(folder):
        raise OSError("disk nearly full: no snapshot")
    path = os.path.join(folder, f"snapshot-{int(time.time() * 1000)}.db")
    store.snapshot(path)
    return path
