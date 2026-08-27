#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

read_env_value() {
  local key="$1"
  local value="${!key:-}"
  if [[ -n "$value" ]]; then printf '%s' "$value"; return; fi
  if [[ -f .env ]]; then
    value="$(awk -F= -v wanted="$key" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' .env)"
    value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
    printf '%s' "$value"
  fi
}

SSH_KEY_PATH="$(read_env_value SSH_KEY_PATH)"
SSH_KNOWN_HOSTS_PATH="$(read_env_value SSH_KNOWN_HOSTS_PATH)"
SERVER_HOST="$(read_env_value SERVER_HOST)"
SERVER_USER="$(read_env_value SERVER_USER)"
SERVER_TARGET_DIR="$(read_env_value SERVER_TARGET_DIR)"
SYSTEMD_SERVICE="$(read_env_value SYSTEMD_SERVICE)"
REMOTE_PYTHON_BIN="$(read_env_value REMOTE_PYTHON_BIN)"
LOCAL_PYTHON="${LOCAL_PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
SKIP_LOCAL_CHECKS="${SKIP_LOCAL_CHECKS:-0}"

: "${SSH_KEY_PATH:?SSH_KEY_PATH가 필요합니다}"
: "${SSH_KNOWN_HOSTS_PATH:?SSH_KNOWN_HOSTS_PATH가 필요합니다}"
: "${SERVER_HOST:?SERVER_HOST가 필요합니다}"
SERVER_USER="${SERVER_USER:-ubuntu}"
SERVER_TARGET_DIR="${SERVER_TARGET_DIR:-/home/ubuntu/JH_HOLDINGS}"
SYSTEMD_SERVICE="${SYSTEMD_SERVICE:-jh_holdings_bot}"
REMOTE_PYTHON_BIN="${REMOTE_PYTHON_BIN:-python3.12}"

if [[ "$SERVER_TARGET_DIR" != /home/*/JH_HOLDINGS && "$SERVER_TARGET_DIR" != /opt/JH_HOLDINGS ]]; then
  echo "안전하지 않은 SERVER_TARGET_DIR입니다: $SERVER_TARGET_DIR" >&2; exit 1
fi
if [[ "$SYSTEMD_SERVICE" != "jh_holdings_bot" ]]; then
  echo "SYSTEMD_SERVICE는 jh_holdings_bot만 허용합니다." >&2; exit 1
fi
if [[ ! -f "$SSH_KEY_PATH" ]]; then echo "SSH 키 파일이 없습니다." >&2; exit 1; fi
if [[ ! -s "$SSH_KNOWN_HOSTS_PATH" ]]; then echo "검증된 SSH known_hosts 파일이 없습니다." >&2; exit 1; fi
if [[ "$SKIP_LOCAL_CHECKS" != "0" && "$SKIP_LOCAL_CHECKS" != "1" ]]; then exit 1; fi
if [[ -n "$(git status --porcelain)" ]]; then echo "배포 전 Git 작업트리가 깨끗해야 합니다." >&2; exit 1; fi
if [[ "$(git branch --show-current)" != "main" ]]; then echo "main 브랜치에서만 배포할 수 있습니다." >&2; exit 1; fi

git fetch origin main --no-tags
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
  echo "로컬 main이 origin/main과 정확히 일치해야 합니다." >&2; exit 1
fi
if [[ "$SKIP_LOCAL_CHECKS" == "0" ]]; then
  "$LOCAL_PYTHON" -m pytest
  "$LOCAL_PYTHON" -m ruff check .
  "$LOCAL_PYTHON" -m jd_holdings.cli --config strategy.yaml validate-config
fi

grep -q 'JDSS-3.2.2-RS6M-ONEWAY-HWM75' strategy.yaml
grep -q 'live_enabled: false' strategy.yaml
COMMIT_SHA="$(git rev-parse HEAD)"
ARCHIVE_PATH="$(mktemp "/tmp/jh_holdings_live_${COMMIT_SHA}.XXXXXX.tar.gz")"
SERVICE_PATH="$(mktemp "/tmp/jh_holdings_live_service.XXXXXX")"
trap 'rm -f "$ARCHIVE_PATH" "$SERVICE_PATH"' EXIT

git archive --format=tar.gz --output="$ARCHIVE_PATH" HEAD
sed -e "s|__SERVER_USER__|$SERVER_USER|g" -e "s|__TARGET_DIR__|$SERVER_TARGET_DIR|g" \
  systemd/jh_holdings_bot.service.template > "$SERVICE_PATH"

SSH_ARGS=(
  -o BatchMode=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$SSH_KNOWN_HOSTS_PATH"
  -i "$SSH_KEY_PATH"
)
REMOTE_ARCHIVE="/tmp/jh_holdings_live_${COMMIT_SHA}.tar.gz"
REMOTE_SERVICE="/tmp/${SYSTEMD_SERVICE}.live.service"
scp "${SSH_ARGS[@]}" "$ARCHIVE_PATH" "$SERVER_USER@$SERVER_HOST:$REMOTE_ARCHIVE"
scp "${SSH_ARGS[@]}" "$SERVICE_PATH" "$SERVER_USER@$SERVER_HOST:$REMOTE_SERVICE"

ssh "${SSH_ARGS[@]}" "$SERVER_USER@$SERVER_HOST" bash -s -- \
  "$SERVER_TARGET_DIR" "$COMMIT_SHA" "$REMOTE_ARCHIVE" "$REMOTE_SERVICE" \
  "$SYSTEMD_SERVICE" "$REMOTE_PYTHON_BIN" <<'REMOTE'
set -Eeuo pipefail

target_dir="$1"
commit_sha="$2"
remote_archive="$3"
remote_service="$4"
service_name="$5"
remote_python="$6"
release_dir="$target_dir/releases/$commit_sha"
shared_dir="$target_dir/shared"
env_file="$shared_dir/.env"
live_db="$shared_dir/data/jdss-live.db"
service_unit="/etc/systemd/system/${service_name}.service"
unit_backup="/tmp/${service_name}.live.before-${commit_sha}.service"
previous_current="$(readlink -f "$target_dir/current" 2>/dev/null || true)"
backup_path=""
rollback_armed=0
restore_db_allowed=1

cleanup() {
  sudo rm -f "$unit_backup"
  rm -f "$remote_archive" "$remote_service"
}

assert_live_markers() {
  local release="$1"
  "$release/.venv/bin/python" - "$live_db" <<'PY'
import sqlite3
import sys

connection = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
try:
    values = dict(connection.execute(
        "SELECT key, value FROM system_state "
        "WHERE key IN ('live_commissioned','operator_buy_halt')"
    ).fetchall())
finally:
    connection.close()
assert values.get("live_commissioned") == "1", values
assert values.get("operator_buy_halt") == "1", values
print("LIVE_COMMISSIONED=PASS BUY_HALT=1")
PY
}

rollback() {
  local rc=$?
  trap - ERR
  set +e
  if [[ "$rollback_armed" == "1" ]]; then
    echo "실거래 안전 배포 실패: 직전 운영 프로그램으로 자동 복구합니다." >&2
    sudo systemctl stop "$service_name" >/dev/null 2>&1 || true

    if [[ "$restore_db_allowed" == "1" && -n "$backup_path" && -f "$backup_path" ]]; then
      rm -f "${live_db}-wal" "${live_db}-shm"
      cp "$backup_path" "$live_db"
      chmod 600 "$live_db"
    else
      echo "서비스 시작 이후이므로 실제 상태 보존을 위해 실거래 DB를 과거로 되돌리지 않습니다." >&2
    fi

    if [[ -n "$previous_current" && -d "$previous_current" ]]; then
      ln -sfn "$previous_current" "$target_dir/current.rollback"
      mv -Tf "$target_dir/current.rollback" "$target_dir/current"
    fi
    if [[ -f "$unit_backup" ]]; then
      sudo install -m 0644 "$unit_backup" "$service_unit"
    fi
    sudo systemctl daemon-reload
    sudo systemctl start "$service_name" >/dev/null 2>&1 || true
    if sudo systemctl is-active --quiet "$service_name"; then
      echo "자동 복구 성공: 직전 실거래 프로그램이 매수 잠금 상태로 재시작됐습니다." >&2
    else
      echo "자동 복구 실패: 신규 매수를 금지하고 수동 복구가 필요합니다." >&2
    fi
  fi
  cleanup
  exit "$rc"
}

command -v "$remote_python" >/dev/null
"$remote_python" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'
test -f "$env_file"
test -f "$live_db"
test -n "$previous_current"
test -d "$previous_current"
sudo systemctl is-active --quiet "$service_name"
sudo test -f "$service_unit"

set -a
source "$env_file"
set +a
test "${JDSS_TRADING_MODE:-}" = "live"
test "${JDSS_LIVE_CONFIRMATION:-}" = "ENABLE_JDSS_LIVE_ORDERS"
test "${JDSS_DB_PATH:-}" = "$live_db"
: "${TOSS_APP_KEY:?Oracle 환경파일에 TOSS_APP_KEY가 필요합니다}"
: "${TOSS_APP_SECRET:?Oracle 환경파일에 TOSS_APP_SECRET가 필요합니다}"

test -x "$previous_current/.venv/bin/jdss"
grep -q 'live_enabled: false' "$previous_current/strategy.yaml"
assert_live_markers "$previous_current"
"$previous_current/.venv/bin/jdss" --config "$previous_current/strategy.yaml" live-release-check

# 서비스가 계속 동작하는 동안 새 release를 먼저 설치하고 검증합니다.
mkdir -p "$target_dir/releases" "$shared_dir/backups"
if [[ -d "$release_dir" && "$(readlink -f "$release_dir")" != "$previous_current" ]]; then
  rm -rf "$release_dir"
fi
mkdir -p "$release_dir"
tar -xzf "$remote_archive" -C "$release_dir"
"$remote_python" -m venv "$release_dir/.venv"
"$release_dir/.venv/bin/python" -m pip install --upgrade pip
"$release_dir/.venv/bin/python" -m pip install \
  --constraint "$release_dir/requirements.lock" "$release_dir"
"$release_dir/.venv/bin/jdss" --config "$release_dir/strategy.yaml" validate-config
grep -q 'live_enabled: false' "$release_dir/strategy.yaml"

# 전략 수학·자금·DB 계약 변경은 정기 코드 갱신에서 금지합니다.
# 2026-09-01 실거래 준비에서 승인된 단 하나의 예외는
# 장전·장후 전용에서 장전·정규장·장후로 바꾸는 주문 세션 확장입니다.
"$remote_python" - "$previous_current/strategy.yaml" "$release_dir/strategy.yaml" <<'PY'
import copy
import sys

import yaml

with open(sys.argv[1], encoding="utf-8") as stream:
    previous = yaml.safe_load(stream)
with open(sys.argv[2], encoding="utf-8") as stream:
    candidate = yaml.safe_load(stream)

previous_sessions = previous["global"]["trading_sessions"]
candidate_sessions = candidate["global"]["trading_sessions"]
approved_before = {"regular": False, "after_hours": True, "pre_market": True}
approved_after = {"regular": True, "after_hours": True, "pre_market": True}
if previous_sessions not in (approved_before, approved_after):
    raise SystemExit(f"기존 주문 세션 계약이 승인 범위가 아닙니다: {previous_sessions}")
if candidate_sessions != approved_after:
    raise SystemExit(f"새 주문 세션 계약이 승인값과 다릅니다: {candidate_sessions}")

previous_without_sessions = copy.deepcopy(previous)
candidate_without_sessions = copy.deepcopy(candidate)
previous_without_sessions["global"].pop("trading_sessions")
candidate_without_sessions["global"].pop("trading_sessions")
if previous_without_sessions != candidate_without_sessions:
    raise SystemExit("주문 세션 외 strategy.yaml 변경은 실거래 정기 배포에서 금지됩니다")
print("LIVE_STRATEGY_CONTRACT=PASS regular_session_enabled=1")
PY
cmp -s \
  "$previous_current/src/jd_holdings/application/database.py" \
  "$release_dir/src/jd_holdings/application/database.py" || {
  echo "실거래 정기 배포에서 DB 스키마 코드 변경은 금지됩니다." >&2
  exit 1
}

# 정기 실거래 배포는 전략 세대를 자동 변경하지 않습니다.
"$remote_python" - "$previous_current/strategy.yaml" "$release_dir/strategy.yaml" <<'PY'
import re
import sys
from pathlib import Path

pattern = re.compile(r'^config_version:\s*["\']?([^"\'\s]+)', re.MULTILINE)
values = []
for raw in sys.argv[1:]:
    match = pattern.search(Path(raw).read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit(f"config_version을 읽을 수 없습니다: {raw}")
    values.append(match.group(1))
if values[0] != values[1]:
    raise SystemExit(f"실거래 정기 배포에서 전략 세대 변경 금지: {values[0]} -> {values[1]}")
PY

assert_live_markers "$release_dir"
"$release_dir/.venv/bin/jdss" --config "$release_dir/strategy.yaml" live-release-check

sudo cp "$service_unit" "$unit_backup"
sudo chmod 600 "$unit_backup"
rollback_armed=1
trap rollback ERR
sudo systemctl stop "$service_name"
if sudo systemctl is-active --quiet "$service_name"; then
  echo "실거래 서비스를 정지하지 못했습니다." >&2
  exit 1
fi

# 서비스가 멈춘 일관된 시점의 실거래 원장을 보관합니다.
backup_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
previous_sha="$(basename "$previous_current")"
backup_path="$shared_dir/backups/jdss-live-${backup_stamp}-${previous_sha}.db"
"$remote_python" - "$live_db" "$backup_path" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
chmod 600 "$backup_path"

sudo install -m 0644 "$remote_service" "$service_unit"
sudo sed -i \
  "s|^Environment=JDSS_DB_PATH=.*$|Environment=JDSS_DB_PATH=$live_db|" \
  "$service_unit"
sudo grep -q "^Environment=JDSS_DB_PATH=$live_db$" "$service_unit"
ln -sfn "$release_dir" "$target_dir/current.new"
mv -Tf "$target_dir/current.new" "$target_dir/current"
sudo systemctl daemon-reload

# 새 코드로 시작하기 전 마지막 읽기 전용 점검입니다.
"$target_dir/current/.venv/bin/jdss" --config "$target_dir/current/strategy.yaml" validate-config
assert_live_markers "$target_dir/current"
"$target_dir/current/.venv/bin/jdss" --config "$target_dir/current/strategy.yaml" live-release-check
"$target_dir/current/.venv/bin/jdss" --config "$target_dir/current/strategy.yaml" toss-smoke

# 여기부터 자동 매도가 가능하므로 실패 시 DB는 실제 상태 보존을 위해 복원하지 않습니다.
restore_db_allowed=0
sudo systemctl start "$service_name"
sudo systemctl is-active --quiet "$service_name"
test "$(readlink -f "$target_dir/current")" = "$release_dir"
assert_live_markers "$target_dir/current"

# 새 봇이 확정된 운영자 메뉴를 Telegram에 등록했는지 읽기 전용으로 확인합니다.
"$target_dir/current/.venv/bin/python" - <<'PY'
import json
import os
import time
import urllib.parse
import urllib.request

token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if not token:
    raise SystemExit("Telegram bot token이 없습니다")
expected = [
    "dashboard", "today", "account", "portfolio",
    "backtest", "onboarding", "halt", "help",
]
url = f"https://api.telegram.org/bot{token}/getMyCommands"
for attempt in range(5):
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    commands = [item.get("command") for item in payload.get("result", [])]
    if commands == expected:
        print("TELEGRAM_OPERATOR_MENU=PASS")
        break
    if attempt == 4:
        raise SystemExit("Telegram 운영자 메뉴가 새 버전과 일치하지 않습니다")
    time.sleep(2)
PY

rollback_armed=0
trap - ERR
cleanup
echo "Oracle live-safe deploy verified: commit=$commit_sha BUY_HALT=1"
REMOTE

echo "실거래 안전 배포 완료: $COMMIT_SHA (기존 live DB 보존, 신규 매수 잠금 유지)"
