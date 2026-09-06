from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize("kind,passes", [("fresh", True), ("stale", False), ("missing", False),
                                       ("naive", False), ("legacy", True), ("uninitialized", False)])
def test_external_health_checks_real_sqlite_heartbeat(tmp_path, kind, passes):
    workflow = yaml.safe_load(
        (Path(__file__).parents[1] / ".github/workflows/oracle-health-watch.yml").read_text()
    )
    step = next(step for step in workflow["jobs"]["health"]["steps"] if step.get("id") == "verify")
    code = step["run"].split("<<'PY'\n", 1)[1].split("\nPY", 1)[0]
    path = tmp_path / "health.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE system_state(key TEXT PRIMARY KEY, value TEXT)")
        values = {"live_commissioned": "1", "operator_buy_halt": "1"}
        if kind != "uninitialized":
            values["jh_auto_watchdog_version"] = "1"
        stamp = datetime.now(UTC)
        if kind == "stale":
            stamp -= timedelta(minutes=10)
        if kind == "naive":
            stamp = stamp.replace(tzinfo=None)
        if kind != "missing":
            values["jh_auto_scheduler_heartbeat"] = stamp.isoformat()
        connection.executemany("INSERT INTO system_state VALUES (?,?)", values.items())
    release = tmp_path / "release" if kind == "legacy" else Path(__file__).parents[1]
    result = subprocess.run(
        [sys.executable, "-c", code, str(path), "live", str(release)], capture_output=True, text=True,
    )
    assert (result.returncode == 0) is passes, result.stderr
    if kind == "legacy":
        assert "UNAVAILABLE" in result.stdout
    elif passes:
        assert "SCHEDULER_HEARTBEAT=PASS" in result.stdout
