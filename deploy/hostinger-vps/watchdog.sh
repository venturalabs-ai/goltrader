#!/usr/bin/env bash
# Watchdog do Goltrader (rodado a cada minuto pelo systemd timer). Checa /api/saude e a saúde dos containers;
# após N falhas seguidas reinicia o que estiver ruim e avisa por Telegram (opcional). Também avisa se o kill switch
# ou a trava de perda diária forem acionados. Estado em $STATE_DIR/watchdog.
set -Eeuo pipefail
LOG_TAG=watchdog
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

THRESHOLD="${GOLTRADER_WATCHDOG_FAILS:-3}"          # falhas consecutivas antes de agir
COOLDOWN="${GOLTRADER_WATCHDOG_COOLDOWN:-600}"      # segundos mínimos entre reinícios
WD_DIR="$STATE_DIR/watchdog"
mkdir -p "$WD_DIR"
exec 9>"$WD_DIR/lock"; flock -n 9 || exit 0         # uma execução por vez

now="$(date +%s)"
read_num() { local v; v="$(cat "$1" 2>/dev/null || true)"; case "$v" in ''|*[!0-9]*) echo 0 ;; *) echo "$v" ;; esac; }
fails="$(read_num "$WD_DIR/fails")"
last_restart="$(read_num "$WD_DIR/last_restart")"
was_down=0; [ -f "$WD_DIR/down" ] && was_down=1

problems=()
[ -n "$(current_tag)" ] || { log "sem tag atual (nada implantado ainda): nada a vigiar"; exit 0; }
api_saude_ok || problems+=("api: /api/saude sem resposta ok")
bad_services=()
for svc in api worker web; do
  h="$(service_health "$svc")"
  case "$h" in healthy|running) ;; starting) ;; *) problems+=("$svc: $h"); bad_services+=("$svc") ;; esac
done

if [ "${#problems[@]}" -eq 0 ]; then
  [ "$fails" -eq 0 ] || log "recuperado após $fails falha(s)"
  echo 0 > "$WD_DIR/fails"
  if [ "$was_down" = 1 ]; then rm -f "$WD_DIR/down"; notify_telegram "Goltrader VPS: RECUPERADO (tag $(current_tag))."; fi
  # Avisos de segurança do worker (kill switch / trava diária), uma vez por mudança de estado
  wcid="$(dc ps -q worker 2>/dev/null | head -n 1 || true)"
  if [ -n "$wcid" ]; then
    msg="$("$DOCKER" exec "$wcid" python -m execution.healthcheck 90 2>/dev/null || true)"
    flags=""
    case "$msg" in *kill=True*) flags="KILL_SWITCH ativo" ;; esac
    case "$msg" in *halt=None*|'') ;; *halt=*) flags="${flags:+$flags; }operação TRAVADA (perda diária)" ;; esac
    prev_flags="$(cat "$WD_DIR/flags" 2>/dev/null || true)"
    if [ "$flags" != "$prev_flags" ]; then
      printf '%s' "$flags" > "$WD_DIR/flags"
      [ -z "$flags" ] || notify_telegram "Goltrader VPS: $flags. Veja docs/DEPLOY-VPS.md."
      log "estado de segurança: ${flags:-normal}"
    fi
  fi
  exit 0
fi

fails=$((fails + 1)); echo "$fails" > "$WD_DIR/fails"
log "falha $fails/$THRESHOLD: ${problems[*]}"
[ "$fails" -ge "$THRESHOLD" ] || exit 1

if [ "$was_down" = 0 ]; then
  : > "$WD_DIR/down"
  notify_telegram "Goltrader VPS: PROBLEMA (${problems[*]}). Tentando reiniciar."
fi
if [ $((now - last_restart)) -ge "$COOLDOWN" ]; then
  echo "$now" > "$WD_DIR/last_restart"
  if [ "${#bad_services[@]}" -gt 0 ]; then
    log "reiniciando: ${bad_services[*]}"
    dc restart "${bad_services[@]}" || log "falha ao reiniciar ${bad_services[*]}"
  else
    log "reiniciando api (saude sem resposta)"
    dc restart api || log "falha ao reiniciar api"
  fi
else
  log "reinício recente (cooldown ${COOLDOWN}s): aguardando"
fi
exit 1
