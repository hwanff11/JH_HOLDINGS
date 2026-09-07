#!/usr/bin/env bash
set -Eeuo pipefail

UPSTREAM_REPO="https://github.com/chrisryugj/korean-law-mcp.git"
UPSTREAM_COMMIT="${LEGAL_MCP_UPSTREAM_COMMIT:-ba10add7c6359ca1587b420153b7cd66eba28d30}"
BASE_DIR="${LEGAL_MCP_BASE_DIR:-/opt/jh_legal_mcp}"
SRC_DIR="${BASE_DIR}/src"
ENV_FILE="${BASE_DIR}/.env"
SECRETS_FILE="${1:-}"
CONTAINER_NAME="jh-legal-mcp"
HOST_PORT="${LEGAL_MCP_HOST_PORT:-3100}"
IMAGE_NAME="jh-korean-law-mcp:${UPSTREAM_COMMIT:0:12}"

log() { printf '[jh-legal-mcp] %s\n' "$*"; }
die() { printf '[jh-legal-mcp] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "run as root"
[[ "$UPSTREAM_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "UPSTREAM_COMMIT must be a 40-char git SHA"
[[ "$HOST_PORT" =~ ^[0-9]+$ ]] || die "HOST_PORT must be numeric"

export DEBIAN_FRONTEND=noninteractive

install_packages=()
command -v git >/dev/null 2>&1 || install_packages+=(git)
command -v curl >/dev/null 2>&1 || install_packages+=(curl)
command -v openssl >/dev/null 2>&1 || install_packages+=(openssl)
command -v docker >/dev/null 2>&1 || install_packages+=(docker.io)
if ((${#install_packages[@]})); then
  log "installing required packages: ${install_packages[*]}"
  apt-get update -y
  apt-get install -y ca-certificates "${install_packages[@]}"
fi

systemctl enable --now docker >/dev/null 2>&1 || die "failed to start docker"
docker info >/dev/null 2>&1 || die "docker daemon unavailable"

mkdir -p "$BASE_DIR"
chmod 750 "$BASE_DIR"

if [[ -d "$SRC_DIR/.git" ]]; then
  log "refreshing upstream checkout"
  git -C "$SRC_DIR" remote set-url origin "$UPSTREAM_REPO"
else
  log "cloning upstream repository"
  rm -rf "$SRC_DIR"
  git clone --filter=blob:none --no-checkout "$UPSTREAM_REPO" "$SRC_DIR"
fi

git -C "$SRC_DIR" fetch --no-tags --depth 1 origin "$UPSTREAM_COMMIT"
git -C "$SRC_DIR" checkout --detach --force "$UPSTREAM_COMMIT"
[[ "$(git -C "$SRC_DIR" rev-parse HEAD)" == "$UPSTREAM_COMMIT" ]] || die "upstream pin verification failed"

if [[ ! -f "$ENV_FILE" ]]; then
  umask 077
  auth_token="$(openssl rand -hex 32)"
  cat > "$ENV_FILE" <<ENVEOF
LAW_OC=
LAW_API_PROTOCOL=https
MCP_AUTH_TOKEN=${auth_token}
MCP_HTTP_HOST=0.0.0.0
NODE_ENV=production
MCP_MAX_UPSTREAM_REQUESTS=48
MCP_MAX_BATCH_CALLS=20
ENVEOF
  unset auth_token
  chmod 600 "$ENV_FILE"
  log "created private environment file with generated MCP auth token"
fi

# Optional one-way secret injection from the GitHub environment. Existing LAW_OC
# is preserved when no new value is supplied.
if [[ -n "$SECRETS_FILE" && -f "$SECRETS_FILE" ]]; then
  new_law_oc="$(sed -n 's/^LAW_OC=//p' "$SECRETS_FILE" | head -n 1)"
  if [[ -n "$new_law_oc" ]]; then
    tmp_env="$(mktemp)"
    awk -v v="$new_law_oc" '
      BEGIN { replaced=0 }
      /^LAW_OC=/ { print "LAW_OC=" v; replaced=1; next }
      { print }
      END { if (!replaced) print "LAW_OC=" v }
    ' "$ENV_FILE" > "$tmp_env"
    install -m 600 "$tmp_env" "$ENV_FILE"
    rm -f "$tmp_env"
    log "updated LAW_OC from deployment secret"
  fi
  rm -f "$SECRETS_FILE"
fi

# Never log secret values. Validate only that required auth exists.
grep -Eq '^MCP_AUTH_TOKEN=.+$' "$ENV_FILE" || die "MCP_AUTH_TOKEN missing from $ENV_FILE"

log "building pinned image $IMAGE_NAME"
docker build --pull -t "$IMAGE_NAME" "$SRC_DIR"

# Remove only our own previous container before port ownership validation.
if docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
  docker rm -f "$CONTAINER_NAME" >/dev/null
fi

if ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)${HOST_PORT}$"; then
  die "127.0.0.1:${HOST_PORT} is already in use by another service"
fi

log "starting isolated container on loopback ${HOST_PORT}->3000"
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --env-file "$ENV_FILE" \
  --publish "127.0.0.1:${HOST_PORT}:3000" \
  --memory 512m \
  --cpus 1.0 \
  --pids-limit 256 \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  "$IMAGE_NAME" >/dev/null

healthy=0
for _ in $(seq 1 30); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${HOST_PORT}/health" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 1
done

if [[ "$healthy" -ne 1 ]]; then
  docker logs --tail 120 "$CONTAINER_NAME" >&2 || true
  die "health check failed"
fi

law_oc_status="missing"
if grep -Eq '^LAW_OC=.+$' "$ENV_FILE"; then
  law_oc_status="configured"
fi

log "deployment complete"
log "upstream_commit=$UPSTREAM_COMMIT"
log "container=$CONTAINER_NAME"
log "endpoint=http://127.0.0.1:${HOST_PORT} (loopback only)"
log "LAW_OC=$law_oc_status"
log "MCP_AUTH_TOKEN=configured (value intentionally not printed)"
docker ps --filter "name=^/${CONTAINER_NAME}$" --format 'container={{.Names}} status={{.Status}} ports={{.Ports}}'
