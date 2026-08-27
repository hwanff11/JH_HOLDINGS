from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path


def integrity_check(path: Path) -> None:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    if row is None or row[0] != "ok":
        raise RuntimeError(f"SQLite integrity_check failed: {row}")


def create_backup(source: Path, backup_dir: Path, retention_days: int) -> Path:
    if retention_days < 1:
        raise ValueError("retention_days must be >= 1")
    if not source.is_file():
        raise FileNotFoundError(source)

    backup_dir.mkdir(parents=True, exist_ok=True)
    integrity_check(source)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"jdss-live-daily-{stamp}.db"
    fd, temporary_name = tempfile.mkstemp(prefix=".jdss-live-", suffix=".db", dir=backup_dir)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
        target_connection = sqlite3.connect(temporary, timeout=30)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        integrity_check(temporary)
        os.chmod(temporary, 0o600)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    for candidate in backup_dir.glob("jdss-live-daily-*.db"):
        if candidate == destination:
            continue
        modified = datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC)
        if modified < cutoff:
            candidate.unlink()

    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="JDSS live SQLite online backup")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--retention-days", type=int, default=90)
    args = parser.parse_args()
    backup = create_backup(args.source, args.backup_dir, args.retention_days)
    print(f"LIVE_DB_BACKUP=PASS path={backup}")


if __name__ == "__main__":
    main()
