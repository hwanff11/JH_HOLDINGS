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

: "${SSH_KEY_PATH:?SSH_KEY_PATH가 필요합니다}"
: "${SSH_KNOWN_HOSTS_PATH:?SSH_KNOWN_HOSTS_PATH가 필요합니다}"
: "${SERVER_HOST:?SERVER_HOST가 필요합니다}"
SERVER_USER="${SERVER_USER:-ubuntu}"
SERVER_TARGET_DIR="${SERVER_TARGET_DIR:-/home/ubuntu/JH_HOLDINGS}"
SYSTEMD_SERVICE="${SYSTEMD_SERVICE:-jh_holdings_bot}"

if [[ "$SYSTEMD_SERVICE" != "jh_holdings_bot" ]]; then
  echo "SYSTEMD_SERVICE는 jh_holdings_bot만 허용합니다." >&2; exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then echo "commission 전 Git 작업트리가 깨끗해야 합니다." >&2; exit 1; fi
if [[ "$(git branch --show-current)" != "main" ]]; then echo "main에서만 commission할 수 있습니다." >&2; exit 1; fi
git fetch origin main --no-tags
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
grep -q 'live_enabled: true' strategy.yaml
commit_sha="$(git rev-parse HEAD)"

SSH_ARGS=(
  -o BatchMode=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$SSH_KNOWN_HOSTS_PATH"
  -i "$SSH_KEY_PATH"
)

ssh "${SSH_ARGS[@]}" "$SERVER_USER@$SERVER_HOST" bash -s -- \
  "$SERVER_TARGET_DIR" "$commit_sha" "$SYSTEMD_SERVICE" <<'REMOTE'
set -Eeuo pipefail

target_dir="$1"
commit_sha="$2"
service_name="$3"
current_dir="$(readlink -f "$target_dir/current")"
shared_dir="$target_dir/shared"
env_file="$shared_dir/.env"
live_db="$shared_dir/data/jdss-live.db"
candidate_db="$shared_dir/data/jdss-live-candidate-${commit_sha}.db"
env_backup="$shared_dir/backups/env-before-live-armed-${commit_sha}.env"

if [[ "$(basename "$current_dir")" != "$commit_sha" ]]; then
  echo "Oracle current가 commission 대상 main SHA와 다릅니다." >&2
  exit 1
fi
if [[ ! -f "$env_file" ]]; then
  echo "Oracle shared .env가 없습니다." >&2
  exit 1
fi
if [[ -e "$live_db" ]]; then
  echo "기존 live DB가 있어 최초 commissioning을 중단합니다: $live_db" >&2
  exit 1
fi
if [[ ! -x "$current_dir/.venv/bin/jdss" || ! -x "$current_dir/.venv/bin/jdss-bot" ]]; then
  echo "현재 release executable이 없습니다." >&2
  exit 1
fi
grep -q 'live_enabled: true' "$current_dir/strategy.yaml"

mkdir -p "$shared_dir/data" "$shared_dir/backups"
rm -f "$candidate_db" "${candidate_db}-wal" "${candidate_db}-shm"
cp "$env_file" "$env_backup"
chmod 600 "$env_backup"

rollback() {
  local rc=$?
  trap - ERR
  set +e
  echo "LIVE-ARMED commissioning 실패: dry-run 환경으로 복구합니다." >&2
  sudo systemctl stop "$service_name" >/dev/null 2>&1 || true
  if [[ -f "$env_backup" ]]; then
    cp "$env_backup" "$env_file"
    chmod 600 "$env_file"
  fi
  sudo systemctl start "$service_name" >/dev/null 2>&1 || true
  if [[ -f "$candidate_db" ]]; then
    mv "$candidate_db" "${candidate_db}.failed" 2>/dev/null || true
  fi
  exit "$rc"
}
trap rollback ERR

# First-use preflight is read-only against Toss. It writes only to the temporary
# candidate SQLite ledger and arms BUY halt after all checks pass.
JDSS_TRADING_MODE=live \
JDSS_LIVE_CONFIRMATION=ENABLE_JDSS_LIVE_ORDERS \
JDSS_DB_PATH="$candidate_db" \
JDSS_CONFIG_PATH="$current_dir/strategy.yaml" \
"$current_dir/.venv/bin/jdss" --config "$current_dir/strategy.yaml" live-preflight --arm-buy-halt

"$current_dir/.venv/bin/python" - "$candidate_db" <<'PY'
import sqlite3
import sys

path = sys.argv[1]
connection = sqlite3.connect(path)
values = dict(connection.execute(
    "SELECT key, value FROM system_state WHERE key IN ('live_commissioned','operator_buy_halt')"
).fetchall())
connection.close()
assert values.get('live_commissioned') == '1', values
assert values.get('operator_buy_halt') == '1', values
PY

# Only after the fresh-account preflight passes do we stop dry-run and switch the
# environment. There are still no broker side effects and BUY remains halted.
sudo systemctl stop "$service_name"
mv "$candidate_db" "$live_db"
chmod 600 "$live_db"
sed -i '/^JDSS_TRADING_MODE=/d;/^JDSS_LIVE_CONFIRMATION=/d;/^JDSS_DB_PATH=/d' "$env_file"
printf '\nJDSS_TRADING_MODE=live\nJDSS_LIVE_CONFIRMATION=ENABLE_JDSS_LIVE_ORDERS\nJDSS_DB_PATH=%s\n' "$live_db" >> "$env_file"
chmod 600 "$env_file"

sudo systemctl start "$service_name"
sudo systemctl is-active --quiet "$service_name"

set -a
source "$env_file"
set +a
test "$JDSS_TRADING_MODE" = "live"
test "$JDSS_LIVE_CONFIRMATION" = "ENABLE_JDSS_LIVE_ORDERS"
test "$JDSS_DB_PATH" = "$live_db"

# Live release check is order-write-free: BUY halt, open-order zero and
# broker↔ledger reconciliation must all pass after the process starts.
"$current_dir/.venv/bin/jdss" --config "$current_dir/strategy.yaml" live-release-check
"$current_dir/.venv/bin/jdss" --config "$current_dir/strategy.yaml" toss-smoke
sudo systemctl is-active --quiet "$service_name"

"$current_dir/.venv/bin/python" - "$live_db" <<'PY'
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
values = dict(connection.execute(
    "SELECT key, value FROM system_state WHERE key IN ('live_commissioned','operator_buy_halt')"
).fetchall())
connection.close()
assert values.get('live_commissioned') == '1', values
assert values.get('operator_buy_halt') == '1', values
PY

trap - ERR
echo "LIVE-ARMED commissioning verified: commit=$commit_sha db=$live_db BUY_HALT=1"
REMOTE

echo "LIVE-ARMED 전환 완료: $commit_sha (실계좌 연결, BUY HALT 유지)"
