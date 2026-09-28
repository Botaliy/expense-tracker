import gzip
import sqlite3
from datetime import datetime
from unittest.mock import patch

import pytest

from app import backup


def test_snapshot_is_a_readable_gzipped_copy(tmp_path):
    db_file = tmp_path / "x.db"
    with sqlite3.connect(db_file) as conn:
        conn.execute("create table t (v text)")
        conn.execute("insert into t values ('hello')")

    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(backup.snapshot(db_file)))
    with sqlite3.connect(restored) as conn:
        assert conn.execute("select v from t").fetchall() == [("hello",)]


def test_snapshot_of_missing_database_fails(tmp_path):
    with pytest.raises(backup.BackupError):
        backup.snapshot(tmp_path / "nope.db")


def test_run_once_needs_telegram_settings(client, monkeypatch):
    with pytest.raises(backup.BackupError, match="TELEGRAM"):
        backup.run_once()


def test_run_once_sends_the_database(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t0k")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    get_settings.cache_clear()
    with patch("app.backup.send_to_telegram") as send:
        backup.run_once()
    token, chat_id, filename, content, _ = send.call_args.args
    assert (token, chat_id) == ("t0k", "42")
    assert filename.endswith(".db.gz")
    assert gzip.decompress(content).startswith(b"SQLite format 3")


def test_seconds_until_next_hour():
    assert backup.seconds_until(4, datetime(2026, 9, 28, 3, 30)) == 30 * 60
    assert backup.seconds_until(4, datetime(2026, 9, 28, 4, 0)) == 24 * 3600
