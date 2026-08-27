from __future__ import annotations

import importlib.util
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup_live_db.py"
SPEC = importlib.util.spec_from_file_location("backup_live_db", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
create_backup = MODULE.create_backup
integrity_check = MODULE.integrity_check


def _database(path):
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE ledger(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO ledger(value) VALUES ('live-state')")
        connection.commit()
    finally:
        connection.close()


def test_create_backup_is_consistent_private_and_readable(tmp_path):
    source = tmp_path / "jdss-live.db"
    backup_dir = tmp_path / "backups"
    _database(source)

    backup = create_backup(source, backup_dir, retention_days=90)

    assert backup.exists()
    assert backup.name.startswith("jdss-live-daily-")
    assert oct(backup.stat().st_mode & 0o777) == "0o600"
    integrity_check(backup)
    connection = sqlite3.connect(backup)
    try:
        assert connection.execute("SELECT value FROM ledger").fetchone()[0] == "live-state"
    finally:
        connection.close()


def test_create_backup_removes_only_expired_daily_backups(tmp_path):
    source = tmp_path / "jdss-live.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    _database(source)
    expired = backup_dir / "jdss-live-daily-20260101T000000Z.db"
    retained_deploy = backup_dir / "jdss-live-20260101T000000Z-oldsha.db"
    expired.write_bytes(b"old")
    retained_deploy.write_bytes(b"deploy")
    old = (datetime.now(UTC) - timedelta(days=91)).timestamp()
    os.utime(expired, (old, old))
    os.utime(retained_deploy, (old, old))

    create_backup(source, backup_dir, retention_days=90)

    assert not expired.exists()
    assert retained_deploy.exists()


def test_create_backup_refuses_corrupt_source(tmp_path):
    source = tmp_path / "jdss-live.db"
    source.write_bytes(b"not sqlite")

    with pytest.raises((sqlite3.DatabaseError, RuntimeError)):
        create_backup(source, tmp_path / "backups", retention_days=90)
