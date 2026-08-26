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
grep -q 'live_enabled: true' strategy.yaml
COMMIT_SHA="$(git rev-parse HEAD)"
ARCHIVE_PATH="$(mktemp "/tmp/jh_holdings_${COMMIT_SHA}.XXXXXX.tar.gz")"
SERVICE_PATH="$(mktemp "/tmp/jh_holdings_service.XXXXXX")"
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
REMOTE_ARCHIVE="/tmp/jh_holdings_${COMMIT_SHA}.tar.gz"
REMOTE_SERVICE="/tmp/${SYSTEMD_SERVICE}.service"
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
db_path="$shared_dir/data/jdss.db"
service_unit="/etc/systemd/system/${service_name}.service"
unit_backup="/tmp/${service_name}.service.before-${commit_sha}"
previous_current="$(readlink -f "$target_dir/current" 2>/dev/null || true)"
backup_path=""
had_unit=0
rollback_armed=0

rollback() {
  local rc=$?
  trap - ERR
  set +e
  if [[ "$rollback_armed" == "1" ]]; then
    echo "배포 실패: 직전 Oracle runtime으로 자동 rollback을 시작합니다." >&2
    sudo systemctl stop "$service_name" >/dev/null 2>&1 || true

    if [[ -n "$backup_path" && -f "$backup_path" ]]; then
      rm -f "${db_path}-wal" "${db_path}-shm"
      cp "$backup_path" "$db_path"
      chmod 600 "$db_path"
    fi

    if [[ -n "$previous_current" && -d "$previous_current" ]]; then
      ln -sfn "$previous_current" "$target_dir/current.rollback"
      mv -Tf "$target_dir/current.rollback" "$target_dir/current"
    fi

    if [[ "$had_unit" == "1" && -f "$unit_backup" ]]; then
      sudo install -m 0644 "$unit_backup" "$service_unit"
    else
      sudo rm -f "$service_unit"
    fi
    sudo systemctl daemon-reload
    sudo systemctl start "$service_name" >/dev/null 2>&1 || true
    if sudo systemctl is-active --quiet "$service_name"; then
      echo "자동 rollback 성공: 직전 서비스가 active입니다." >&2
    else
      echo "자동 rollback 실패: 수동 복구가 필요합니다." >&2
    fi
  fi
  sudo rm -f "$unit_backup"
  rm -f "$remote_archive" "$remote_service"
  exit "$rc"
}

command -v "$remote_python" >/dev/null
"$remote_python" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'
mkdir -p "$target_dir/releases" "$shared_dir/data/cache" "$shared_dir/logs" "$shared_dir/backups"
if [[ ! -f "$env_file" ]]; then
  echo "서버의 $env_file를 먼저 생성해야 합니다." >&2
  exit 1
fi
if [[ -z "$previous_current" || ! -d "$previous_current" ]]; then
  echo "표준 배포는 복구 가능한 기존 current release가 필요합니다." >&2
  exit 1
fi

# Standard deployment always remains dry-run even though the codebase now has a
# separately commissioned live capability. It also pins the dry-run DB explicitly.
sed -i '/^JDSS_TRADING_MODE=/d;/^JDSS_LIVE_CONFIRMATION=/d;/^JDSS_DB_PATH=/d' "$env_file"
printf '\nJDSS_TRADING_MODE=dry_run\nJDSS_LIVE_CONFIRMATION=\nJDSS_DB_PATH=%s\n' "$db_path" >> "$env_file"
chmod 600 "$env_file"

# Prepare and validate the new release before stopping the old service.
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
grep -q 'live_enabled: true' "$release_dir/strategy.yaml"

# Standard deploy never crosses config generations. A version change requires a
# dedicated migration plan, backup proof and compatibility tests.
if [[ -f "$previous_current/strategy.yaml" && -f "$db_path" ]]; then
  "$remote_python" - "$previous_current/strategy.yaml" "$release_dir/strategy.yaml" "$db_path" <<'PY'
import re
import sqlite3
import sys
from pathlib import Path

old_path, new_path, db_path = map(Path, sys.argv[1:])
pattern = re.compile(r'^config_version:\s*["\']?([^"\'\s]+)', re.MULTILINE)

def version(path: Path) -> str:
    match = pattern.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit(f"config_version을 읽을 수 없습니다: {path}")
    return match.group(1)

old_version = version(old_path)
new_version = version(new_path)
if old_version == new_version:
    raise SystemExit(0)

connection = sqlite3.connect(db_path)
connection.row_factory = sqlite3.Row
active_positions = connection.execute(
    "SELECT symbol, state, qty FROM positions WHERE state <> 'EMPTY' OR qty <> 0"
).fetchall()
open_orders = connection.execute(
    "SELECT symbol, purpose, status FROM orders "
    "WHERE status IN ('CREATED','SUBMITTED','PENDING','PARTIAL_FILLED',"
    "'PENDING_CANCEL','PENDING_REPLACE','UNKNOWN')"
).fetchall()
connection.close()
raise SystemExit(
    "전략 버전 변경은 표준 deploy에서 금지됩니다. 별도 migration plan·DB 백업·"
    f"호환성 테스트가 필요합니다: {old_version} -> {new_version} "
    f"(active_positions={len(active_positions)}, open_orders={len(open_orders)})"
)
PY
fi

if sudo test -f "$service_unit"; then
  sudo cp "$service_unit" "$unit_backup"
  had_unit=1
fi

rollback_armed=1
trap rollback ERR
sudo systemctl stop "$service_name"

# Take a consistent snapshot only after the service is stopped, so rollback
# cannot lose writes that happened between backup completion and shutdown.
if [[ -f "$db_path" ]]; then
  backup_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  previous_sha="$(basename "$previous_current")"
  backup_path="$shared_dir/backups/jdss-${backup_stamp}-${previous_sha}.db"
  "$remote_python" - "$db_path" "$backup_path" <<'PY'
import sqlite3
import sys
from pathlib import Path

source_path, backup_path = map(Path, sys.argv[1:])
source = sqlite3.connect(source_path)
target = sqlite3.connect(backup_path)
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
  chmod 600 "$backup_path"
fi

sudo install -m 0644 "$remote_service" "$service_unit"
ln -sfn "$release_dir" "$target_dir/current.new"
mv -Tf "$target_dir/current.new" "$target_dir/current"
sudo systemctl daemon-reload
if ! sudo systemctl is-enabled --quiet "$service_name"; then
  sudo systemctl enable "$service_name"
fi

set -a
source "$env_file"
set +a
test "${JDSS_TRADING_MODE:-}" = "dry_run"
test -z "${JDSS_LIVE_CONFIRMATION:-}"
test "${JDSS_DB_PATH:-}" = "$db_path"
"$target_dir/current/.venv/bin/jdss" --config "$target_dir/current/strategy.yaml" init-db
"$target_dir/current/.venv/bin/jdss" --config "$target_dir/current/strategy.yaml" validate-config

test -x "$target_dir/current/.venv/bin/jdss-bot"
sudo systemctl restart "$service_name"
sudo systemctl is-active --quiet "$service_name"
"$target_dir/current/.venv/bin/jdss" --config "$target_dir/current/strategy.yaml" toss-smoke
sudo systemctl is-active --quiet "$service_name"

rollback_armed=0
trap - ERR
sudo rm -f "$unit_backup"
rm -f "$remote_archive" "$remote_service"
echo "Oracle deploy verified: commit=$commit_sha backup=${backup_path:-none} service=$service_name"
REMOTE

echo "배포 완료: $COMMIT_SHA ($SYSTEMD_SERVICE, release venv, forced dry_run, rollback-safe smoke OK)"
