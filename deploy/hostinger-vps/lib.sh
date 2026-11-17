#!/usr/bin/env bash
# Funções comuns do deploy do Goltrader na VPS (usado por atualizar.sh, watchdog.sh, vps-compose.sh).
# Não execute diretamente. Todos os binários são sobrescrevíveis por variável (DOCKER, CURL, GIT) para teste.
# shellcheck shell=bash

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC2034  # usado por quem faz source
REPO_DIR="$(cd "$LIB_DIR/../.." && pwd)"
COMPOSE_DIR="$LIB_DIR"
COMPOSE_FILE="$COMPOSE_DIR/docker-compose.yml"
STATE_DIR="${GOLTRADER_DEPLOY_STATE:-/var/lib/goltrader}"
ENV_FILE="${GOLTRADER_ENV_FILE:-$COMPOSE_DIR/.env}"
DOCKER="${DOCKER:-docker}"
CURL="${CURL:-curl}"
GIT="${GIT:-git}"
PROJECT="${GOLTRADER_PROJECT:-goltrader}"

log() { printf '%s [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "${LOG_TAG:-goltrader}" "$*"; }
die() { log "ERRO: $*" >&2; exit 1; }

# Lê KEY de um arquivo .env SEM executá-lo (sem source): última ocorrência, sem aspas externas.
env_get() {
  local key="$1" file="${2:-$ENV_FILE}" line
  [ -r "$file" ] || { printf ''; return 0; }
  line="$(grep -E "^[[:space:]]*${key}=" "$file" | tail -n 1 || true)"
  line="${line#*=}"
  line="${line%$'\r'}"
  line="${line#\"}"; line="${line%\"}"; line="${line#\'}"; line="${line%\'}"
  printf '%s' "$line"
}

lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

# docker compose do projeto, com a tag informada em $GOLTRADER_TAG_CALL (ou a tag atual gravada).
dc() {
  GOLTRADER_TAG="${GOLTRADER_TAG_CALL:-$(current_tag)}" "$DOCKER" compose \
    --project-name "$PROJECT" --project-directory "$COMPOSE_DIR" -f "$COMPOSE_FILE" "$@"
}

current_tag()  { [ -r "$STATE_DIR/current_tag" ]  && tr -d '[:space:]' < "$STATE_DIR/current_tag"  || printf ''; }
previous_tag() { [ -r "$STATE_DIR/previous_tag" ] && tr -d '[:space:]' < "$STATE_DIR/previous_tag" || printf ''; }

# Estado de saúde de um serviço: healthy | unhealthy | starting | running | restarting | exited | missing
service_health() {
  local cid
  cid="$(dc ps -q "$1" 2>/dev/null | head -n 1 || true)"
  [ -n "$cid" ] || { printf 'missing'; return 0; }
  "$DOCKER" inspect -f '{{if .State.Restarting}}restarting{{else if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || printf 'missing'
}

service_image() {
  local cid
  cid="$(dc ps -q "$1" 2>/dev/null | head -n 1 || true)"
  [ -n "$cid" ] || { printf ''; return 0; }
  "$DOCKER" inspect -f '{{.Config.Image}}' "$cid" 2>/dev/null || printf ''
}

api_local_url() {
  local p="${GOLTRADER_API_LOCAL_PORT:-}"
  [ -n "$p" ] || p="$(env_get GOLTRADER_API_LOCAL_PORT)"
  printf 'http://127.0.0.1:%s/api/saude' "${p:-18000}"
}

api_saude_ok() {
  local body
  body="$("$CURL" -fsS -m 5 "$(api_local_url)" 2>/dev/null)" || return 1
  printf '%s' "$body" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'
}

notify_telegram() {
  # Alerta opcional. Só envia se TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID existirem (ambiente ou .env). Nunca imprime o token.
  local token="${TELEGRAM_BOT_TOKEN:-$(env_get TELEGRAM_BOT_TOKEN)}" chat="${TELEGRAM_CHAT_ID:-$(env_get TELEGRAM_CHAT_ID)}"
  [ -n "$token" ] && [ -n "$chat" ] || return 0
  "$CURL" -fsS -m 10 -o /dev/null --data-urlencode "chat_id=$chat" --data-urlencode "text=$1" \
    "https://api.telegram.org/bot${token}/sendMessage" >/dev/null 2>&1 || log "aviso: falha ao enviar alerta Telegram"
  return 0
}
