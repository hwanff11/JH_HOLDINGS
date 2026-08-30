#!/usr/bin/env bash
set -Eeuo pipefail

SSH_KEY_PATH="${SSH_KEY_PATH:?SSH_KEY_PATH가 필요합니다}"
SSH_KNOWN_HOSTS_PATH="${SSH_KNOWN_HOSTS_PATH:?SSH_KNOWN_HOSTS_PATH가 필요합니다}"
SERVER_HOST="${SERVER_HOST:?SERVER_HOST가 필요합니다}"
SERVER_USER="${SERVER_USER:-ubuntu}"
SERVER_TARGET_DIR="${SERVER_TARGET_DIR:-/home/ubuntu/JH_HOLDINGS}"
SYSTEMD_SERVICE="${SYSTEMD_SERVICE:-jh_holdings_bot}"
REMOTE_PYTHON_BIN="${REMOTE_PYTHON_BIN:-python3.12}"

if [[ "$SERVER_TARGET_DIR" != /home/*/JH_HOLDINGS && "$SERVER_TARGET_DIR" != /opt/JH_HOLDINGS ]]; then
  echo "안전하지 않은 SERVER_TARGET_DIR입니다: $SERVER_TARGET_DIR" >&2
  exit 1
fi
if [[ "$SYSTEMD_SERVICE" != "jh_holdings_bot" ]]; then
  echo "SYSTEMD_SERVICE는 jh_holdings_bot만 허용합니다." >&2
  exit 1
fi
if [[ ! -f "$SSH_KEY_PATH" || ! -s "$SSH_KNOWN_HOSTS_PATH" ]]; then
  echo "검증된 SSH 설정이 필요합니다." >&2
  exit 1
fi

SSH_ARGS=(
  -o BatchMode=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$SSH_KNOWN_HOSTS_PATH"
  -i "$SSH_KEY_PATH"
)

ssh "${SSH_ARGS[@]}" "$SERVER_USER@$SERVER_HOST" bash -s -- \
  "$SERVER_TARGET_DIR" "$SYSTEMD_SERVICE" "$REMOTE_PYTHON_BIN" <<'REMOTE'
set -Eeuo pipefail

target_dir="$1"
service_name="$2"
remote_python="$3"
live_db="$target_dir/shared/data/jdss-live.db"
current="$(readlink -f "$target_dir/current" 2>/dev/null || true)"

command -v "$remote_python" >/dev/null
test -f "$live_db"
test -n "$current"
test -d "$current"
sudo systemctl is-active --quiet "$service_name"

"$remote_python" - "$live_db" <<'PY'
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime

path = sys.argv[1]
now = datetime.now(UTC).isoformat()
connection = sqlite3.connect(path, timeout=15, isolation_level=None)
try:
    connection.execute("PRAGMA busy_timeout=15000")
    connection.execute("BEGIN IMMEDIATE")
    values = dict(
        connection.execute(
            "SELECT key, value FROM system_state "
            "WHERE key IN ('live_commissioned','operator_buy_halt')"
        ).fetchall()
    )
    if values.get("live_commissioned") != "1":
        raise SystemExit("live ledger가 commissioning 완료 상태가 아닙니다")

    connection.execute(
        """
        INSERT INTO system_state(key, value, updated_at) VALUES(?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        ("operator_buy_halt", "1", now),
    )
    connection.execute(
        """
        INSERT INTO system_state(key, value, updated_at) VALUES(?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        ("operator_buy_halt_at", now, now),
    )
    approvals = connection.execute(
        "UPDATE approvals SET status='CANCELED' WHERE status='ACTIVE'"
    ).rowcount
    intents = connection.execute(
        """
        UPDATE cash_release_intents
        SET status='CANCELED', updated_at=?
        WHERE status IN ('WAITING_SGOV_FILL','AWAITING_EXECUTION')
        """,
        (now,),
    ).rowcount
    connection.execute(
        """
        INSERT INTO event_logs(
            severity, event_type, symbol, message, context_json, created_at
        ) VALUES(?, ?, NULL, ?, ?, ?)
        """,
        (
            "SAFE_MODE",
            "LIVE_UPDATE_BUY_HALT_ARMED",
            "실거래 안전 배포를 위해 신규 BUY를 자동 차단했습니다",
            json.dumps(
                {
                    "canceled_approvals": approvals,
                    "canceled_cash_release_intents": intents,
                },
                ensure_ascii=False,
            ),
            now,
        ),
    )
    connection.execute("COMMIT")
except BaseException:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass
    raise
finally:
    connection.close()

print("LIVE_UPDATE_BUY_HALT=ARMED")
PY

# 실행 중이던 BUY가 이미 최종 브로커 경계를 통과한 경우를 잡기 위해
# 짧게 기다린 뒤 기존 release gate가 미체결·정합성을 다시 증명하게 합니다.
sleep 3

"$remote_python" - "$live_db" <<'PY'
import sqlite3
import sys

connection = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=5)
try:
    values = dict(connection.execute(
        "SELECT key, value FROM system_state "
        "WHERE key IN ('live_commissioned','operator_buy_halt')"
    ).fetchall())
finally:
    connection.close()
assert values.get("live_commissioned") == "1", values
assert values.get("operator_buy_halt") == "1", values
print("LIVE_UPDATE_BUY_HALT=VERIFIED")
PY

sudo systemctl is-active --quiet "$service_name"
REMOTE
