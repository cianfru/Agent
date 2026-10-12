import gzip
import json
import os
import shutil
import sqlite3
import time
from collections import namedtuple

import pytest

from synth import backup
from synth.store import Store


def journal_store(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    keep = store.add_pick("alert", "HYPE", "long", 3, 40.0, "t", {})
    gone = store.add_pick("post", "BTC", "long", None, 9.0, "t", {})
    store.void_pick(gone, "draft rejected")
    store.add_post("daily", "text", "draft", keep)
    return store


def test_daily_journal_export_once_a_day_and_kept_30(tmp_path):
    store = journal_store(tmp_path)
    folder = str(tmp_path / "backups")
    now = 1_790_000_000.0
    path = backup.daily(store, folder, now)
    with gzip.open(path, "rt") as f:
        body = json.load(f)
    assert [p["coin"] for p in body["picks"]] == ["HYPE", "BTC"] and body["picks"][1]["void_reason"] == "draft rejected"
    assert body["posts"][0]["text"] == "text" and body["at"] == now
    assert backup.daily(store, folder, now + 60) is None                      # once a day
    for d in range(1, 40):
        backup.daily(store, folder, now + d * 86400)
    files = sorted(os.listdir(folder))
    assert len(files) == backup.KEEP and files[-1].startswith("journal-") and not any(f.endswith(".part") for f in files)


def test_nothing_written_when_the_disk_is_nearly_full(tmp_path, monkeypatch):
    store = journal_store(tmp_path)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _: usage(10**9, 95 * 10**7, 5 * 10**7))
    with pytest.raises(OSError, match="nearly full"):
        backup.daily(store, str(tmp_path / "b"), time.time())
    assert os.listdir(tmp_path / "b") == []


def test_snapshot_is_a_whole_working_database(tmp_path):
    store = journal_store(tmp_path)
    path = backup.snapshot(store, str(tmp_path / "s"))
    db = sqlite3.connect(path)
    assert db.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 2
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
