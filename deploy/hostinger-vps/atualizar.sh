#!/usr/bin/env bash
# Atualiza o Goltrader na VPS: build por commit (tag = sha12), sobe, espera ficar saudável e VOLTA SOZINHO se falhar.
#
#   ./atualizar.sh                      # origin/main
#   ./atualizar.sh <ref>                # branch, tag ou sha
#   ./atualizar.sh --rollback           # volta para a tag anterior (sem build)
#   ./atualizar.sh --sem-git            # usa o checkout como está (sem fetch/checkout)
#   ./atualizar.sh --timeout 300        # espera máxima pela saúde (padrão 240 s)
#   ./atualizar.sh --dry-run            # só valida e mostra o que faria
#   ./atualizar.sh --confirmar-live CONFIRMAR   # EXIGIDO se o .env tiver BYBIT_TESTNET=false (conta real)
set -Eeuo pipefail
LOG_TAG=atualizar
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

REF="origin/main"; NO_GIT=0; ROLLBACK=0; DRY=0; TIMEOUT=240; LIVE_CONFIRM=""
while [ $# -gt 0 ]; do
  case "$1" in
    --rollback) ROLLBACK=1 ;;
    --sem-git) NO_GIT=1 ;;
    --dry-run) DRY=1 ;;
    --timeout) shift; TIMEOUT="${1:?--timeout precisa de segundos}" ;;
    --confirmar-live) shift; LIVE_CONFIRM="${1:-}" ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    -*) die "opção desconhecida: $1" ;;
    *) REF="$1" ;;
  esac
  shift
done

command -v "$DOCKER" >/dev/null || die "docker não encontrado"
command -v flock >/dev/null || die "flock não encontrado (pacote util-linux)"
mkdir -p "$STATE_DIR"

# --- trava de deploy: um deploy por vez ---
exec 9>"${GOLTRADER_DEPLOY_LOCK:-$STATE_DIR/deploy.lock}"
flock -n 9 || die "outro deploy do Goltrader está em andamento (trava $STATE_DIR/deploy.lock)"

# --- pré-requisitos do .env ---
[ -r "$ENV_FILE" ] || die "falta $ENV_FILE (copie .env.example e preencha)"
token="$(env_get GOLTRADER_API_TOKEN)"
[ "${#token}" -ge 24 ] || die "GOLTRADER_API_TOKEN ausente ou curto (<24 caracteres) no .env"
[ -n "$(env_get GOLTRADER_DOMAIN)" ] || die "GOLTRADER_DOMAIN ausente no .env"
export GOLTRADER_API_LOCAL_PORT="${GOLTRADER_API_LOCAL_PORT:-$(env_get GOLTRADER_API_LOCAL_PORT)}"
: "${GOLTRADER_API_LOCAL_PORT:=18000}"; export GOLTRADER_API_LOCAL_PORT

# --- portão de conta real: Testnet é o padrão; LIVE exige TODAS as condições ---
testnet="$(lower "$(env_get BYBIT_TESTNET)")"; [ -n "$testnet" ] || testnet=true
if [ "$testnet" = "false" ]; then
  [ "$(lower "$(env_get GOLTRADER_ALLOW_LIVE)")" = "true" ]   || die "LIVE recusado: GOLTRADER_ALLOW_LIVE precisa ser true"
  [ "$(lower "$(env_get ANALYZER_MODE)")" != "true" ]         || die "LIVE recusado: ANALYZER_MODE=true"
  [ "$LIVE_CONFIRM" = "CONFIRMAR" ]                           || die 'LIVE recusado: passe --confirmar-live CONFIRMAR (só após o dono autorizar)'
  [ "$(env_get GOLTRADER_LIVE_CONFIRMATION)" = "CONFIRMAR" ]  || die "LIVE recusado: GOLTRADER_LIVE_CONFIRMATION=CONFIRMAR ausente no .env"
  [ "$(env_get GOLTRADER_LIVE_HOST)" = "$(hostname)" ]        || die "LIVE recusado: GOLTRADER_LIVE_HOST ('$(env_get GOLTRADER_LIVE_HOST)') != hostname '$(hostname)' (só uma máquina opera real)"
  others="$("$DOCKER" ps --format '{{.Names}}' 2>/dev/null | grep -E 'goltrader.*worker' | grep -v "^${PROJECT}-worker-" || true)"
  [ -z "$others" ] || die "LIVE recusado: outro worker do Goltrader roda nesta máquina: $others"
  log "ATENÇÃO: deploy em modo REAL (LIVE) autorizado por flags + 'CONFIRMAR'"
else
  log "modo: TESTNET"
fi

notify() { notify_telegram "Goltrader VPS: $*"; }

wait_healthy() {  # $1 = tag esperada
  local tag="$1" deadline=$(( $(date +%s) + TIMEOUT )) svc h all img
  local GOLTRADER_TAG_CALL="$tag"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    all=1
    for svc in api worker web; do
      h="$(service_health "$svc")"
      case "$h" in
        healthy) ;;
        unhealthy|restarting|exited|dead|missing) log "serviço $svc: $h"; return 1 ;;
        *) all=0 ;;
      esac
    done
    if [ "$all" = 1 ]; then
      for svc in api web; do
        img="$(service_image "$svc")"
        [ "$img" = "goltrader-$svc:$tag" ] || { log "imagem do $svc é '$img', esperado goltrader-$svc:$tag"; return 1; }
      done
      api_saude_ok && return 0
      log "containers saudáveis mas /api/saude não respondeu ok"
    fi
    sleep 3
  done
  log "tempo esgotado (${TIMEOUT}s) esperando os serviços"
  return 1
}

rollback_to() {  # $1 = tag anterior
  log "ROLLBACK para a tag $1"
  GOLTRADER_TAG_CALL="$1" dc up -d --remove-orphans --no-build
  if GOLTRADER_TAG_CALL="$1" wait_healthy "$1"; then
    log "rollback concluído: tag $1 saudável"; notify "atualização FALHOU; voltei para $1 (saudável)."
  else
    log "ERRO CRÍTICO: rollback para $1 também não ficou saudável"; notify "ERRO CRÍTICO: atualização falhou e o rollback para $1 NÃO ficou saudável. Verifique a VPS."
  fi
}

PREV="$(current_tag)"

# ---------------- rollback manual ----------------
if [ "$ROLLBACK" = 1 ]; then
  target="$(previous_tag)"; [ -n "$target" ] || die "não há tag anterior gravada"
  "$DOCKER" image inspect "goltrader-api:$target" "goltrader-web:$target" >/dev/null 2>&1 || die "imagens da tag $target não existem mais na VPS"
  [ "$DRY" = 0 ] || { log "[dry-run] voltaria de '$PREV' para '$target'"; exit 0; }
  GOLTRADER_TAG_CALL="$target" dc up -d --remove-orphans --no-build
  GOLTRADER_TAG_CALL="$target" wait_healthy "$target" || die "tag $target não ficou saudável"
  printf '%s\n' "$PREV" > "$STATE_DIR/previous_tag"; printf '%s\n' "$target" > "$STATE_DIR/current_tag"
  log "rollback manual concluído: $PREV -> $target"; notify "rollback manual: $PREV -> $target."
  exit 0
fi

# ---------------- git ----------------
if [ "$NO_GIT" = 0 ]; then
  [ -z "$("$GIT" -C "$REPO_DIR" status --porcelain --untracked-files=no)" ] || die "checkout com alterações locais em $REPO_DIR; limpe antes de atualizar"
  log "git fetch origin"
  "$GIT" -C "$REPO_DIR" fetch --quiet --tags origin
  SHA_FULL="$("$GIT" -C "$REPO_DIR" rev-parse --verify "${REF}^{commit}")" || die "ref inválida: $REF"
  [ "$DRY" = 1 ] || "$GIT" -C "$REPO_DIR" checkout --quiet --detach "$SHA_FULL"
else
  SHA_FULL="$("$GIT" -C "$REPO_DIR" rev-parse --verify HEAD)"
fi
TAG="${SHA_FULL:0:12}"
log "commit $SHA_FULL -> tag $TAG (anterior: ${PREV:-nenhuma})"

if [ "$DRY" = 1 ]; then
  bash "$REPO_DIR/scripts/preflight.sh" "$REPO_DIR" || true
  GOLTRADER_TAG_CALL="$TAG" dc config -q && log "[dry-run] compose válido; nada foi alterado"
  exit 0
fi

if [ "${GOLTRADER_SKIP_PREFLIGHT:-0}" != 1 ]; then   # (a variável existe só para os testes com docker falso)
  bash "$REPO_DIR/scripts/preflight.sh" "$REPO_DIR" || die "faltam arquivos para o build (veja acima)"
fi

# ---------------- build ----------------
export DOCKER_BUILDKIT=1
log "build das imagens goltrader-api:$TAG e goltrader-web:$TAG"
if ! GOLTRADER_TAG_CALL="$TAG" dc build; then
  notify "build da tag $TAG falhou; nada foi alterado (continua em ${PREV:-nenhuma})."
  die "build falhou; a versão atual (${PREV:-nenhuma}) continua no ar"
fi

# ---------------- troca ----------------
log "subindo tag $TAG"
if GOLTRADER_TAG_CALL="$TAG" dc up -d --remove-orphans --no-build && wait_healthy "$TAG"; then
  [ -z "$PREV" ] || [ "$PREV" = "$TAG" ] || printf '%s\n' "$PREV" > "$STATE_DIR/previous_tag"
  printf '%s\n' "$TAG" > "$STATE_DIR/current_tag"
  log "OK: Goltrader $TAG saudável (api, worker, web, /api/saude)"
  notify "atualizado para $TAG ($([ "$testnet" = false ] && echo REAL || echo TESTNET)) e saudável."
  # mantém as 3 tags mais novas (as em uso nunca são removidas pelo docker)
  for img in goltrader-api goltrader-web; do
    "$DOCKER" images "$img" --format '{{.Tag}}' 2>/dev/null | tail -n +4 | while read -r t; do
      [ "$t" = "$TAG" ] || [ "$t" = "$PREV" ] || "$DOCKER" image rm "$img:$t" >/dev/null 2>&1 || true
    done
  done
  exit 0
fi

log "FALHA ao subir $TAG; logs recentes:"
dc logs --tail 40 2>&1 | sed 's/^/    /' || true
if [ -n "$PREV" ] && "$DOCKER" image inspect "goltrader-api:$PREV" "goltrader-web:$PREV" >/dev/null 2>&1; then
  rollback_to "$PREV"
else
  log "sem tag anterior para voltar (primeiro deploy): parando os serviços"
  GOLTRADER_TAG_CALL="$TAG" dc stop || true
  notify "primeiro deploy ($TAG) falhou; serviços parados."
fi
exit 1
