#!/usr/bin/env bash
# Testa atualizar.sh (build/health/rollback/portão LIVE) e watchdog.sh com docker/git/curl FALSOS (sem Docker real, sem rede).
# Uso: scripts/tests/test-deploy-scripts.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
FD="$T/fake"; mkdir -p "$FD/images" "$T/bin" "$T/state"
pass=0; failn=0
ok()   { pass=$((pass+1)); printf '  ok   %s\n' "$1"; }
fail() { failn=$((failn+1)); printf '  FAIL %s\n' "$1"; }
check() { if eval "$2"; then ok "$1"; else fail "$1  [$2]"; fi; }

# ---------- docker falso ----------
cat > "$T/bin/docker" <<'FAKE'
#!/usr/bin/env bash
FD="${FAKE_DIR:?}"; echo "docker $*" >> "$FD/calls"
if [ "$1" = compose ]; then
  shift
  while [ $# -gt 0 ]; do case "$1" in --project-name|--project-directory|-f) shift 2 ;; *) break ;; esac; done
  sub="$1"; shift
  case "$sub" in
    build) [ "${FAKE_BUILD_FAIL:-0}" = 1 ] && { echo "build quebrou" >&2; exit 1; }
           touch "$FD/images/goltrader-api:$GOLTRADER_TAG" "$FD/images/goltrader-web:$GOLTRADER_TAG"; exit 0 ;;
    up) echo "$GOLTRADER_TAG" > "$FD/running_tag"; exit 0 ;;
    ps) [ "${1:-}" = -q ] && { echo "cid-$2"; exit 0; }; exit 0 ;;
    restart) echo "restart $*" >> "$FD/restarts"; exit 0 ;;
    *) exit 0 ;;
  esac
elif [ "$1" = inspect ]; then
  fmt="$3"; cid="$4"; svc="${cid#cid-}"; tag="$(cat "$FD/running_tag" 2>/dev/null)"
  case "$fmt" in
    *State.Restarting*) h="$(cat "$FD/health_$tag" 2>/dev/null || echo healthy)"
        [ -f "$FD/svc_$svc" ] && h="$(cat "$FD/svc_$svc")"; echo "$h" ;;
    *Config.Image*) case "$svc" in api|web) echo "goltrader-$svc:$tag" ;; *) echo "goltrader-api:$tag" ;; esac ;;
  esac; exit 0
elif [ "$1" = image ] && [ "$2" = inspect ]; then
  shift 2; for i in "$@"; do [ -e "$FD/images/$i" ] || exit 1; done; exit 0
elif [ "$1" = image ] && [ "$2" = rm ]; then rm -f "$FD/images/$3"; exit 0
elif [ "$1" = images ]; then ls "$FD/images" | grep "^$2:" | sed "s/^$2://" ; exit 0
elif [ "$1" = ps ]; then cat "$FD/ps" 2>/dev/null; exit 0
elif [ "$1" = exec ]; then cat "$FD/hc_msg" 2>/dev/null || echo "ok modo=testnet ws=True kill=False halt=None"; exit 0
fi
exit 0
FAKE
cat > "$T/bin/curl" <<'FAKE'
#!/usr/bin/env bash
FD="${FAKE_DIR:?}"; echo "curl $*" >> "$FD/curls"
case "$*" in *api.telegram.org*) exit 0 ;; esac
tag="$(cat "$FD/running_tag" 2>/dev/null)"; h="$(cat "$FD/health_$tag" 2>/dev/null || echo healthy)"
[ -f "$FD/api_down" ] && exit 22
[ "$h" = healthy ] || exit 22
echo '{"status":"ok","version":"x"}'
FAKE
cat > "$T/bin/git" <<'FAKE'
#!/usr/bin/env bash
# git falso: rev-parse --verify <ref>^{commit} → sha configurado em $FAKE_DIR/ref_<ref com / trocado por _>
FD="${FAKE_DIR:?}"
args=("$@"); [ "$1" = -C ] && args=("${@:3}")
case "${args[0]}" in
  status) exit 0 ;;
  fetch|checkout) exit 0 ;;
  rev-parse) r="${args[2]%\^\{commit\}}"; f="$FD/ref_${r//\//_}"; [ -f "$f" ] && cat "$f" || exit 1 ;;
  *) exit 0 ;;
esac
FAKE
chmod +x "$T/bin/"*
export FAKE_DIR="$FD" DOCKER="$T/bin/docker" CURL="$T/bin/curl" GIT="$T/bin/git"
export GOLTRADER_DEPLOY_STATE="$T/state" GOLTRADER_ENV_FILE="$T/.env" GOLTRADER_SKIP_PREFLIGHT=1
SHA_A=aaaaaaaaaaaa1111111111111111111111111111; SHA_B=bbbbbbbbbbbb2222222222222222222222222222
echo "$SHA_A" > "$FD/ref_origin_main"; echo "$SHA_B" > "$FD/ref_novo"
TOKEN="tokentokentokentokentokentokentoken"
mkenv() { printf 'GOLTRADER_API_TOKEN=%s\nGOLTRADER_DOMAIN=gt.example.com\nBYBIT_TESTNET=true\nTELEGRAM_BOT_TOKEN=123:SEGREDO\nTELEGRAM_CHAT_ID=42\n%s\n' "$TOKEN" "${1:-}" > "$T/.env"; }
ATU="$ROOT/deploy/hostinger-vps/atualizar.sh"; WD="$ROOT/deploy/hostinger-vps/watchdog.sh"

echo "== atualizar.sh =="
mkenv
out="$("$ATU" --timeout 5 2>&1)"; rc=$?
check "primeiro deploy (tag A) sobe e fica saudável" '[ $rc -eq 0 ] && [ "$(cat "$T/state/current_tag")" = aaaaaaaaaaaa ]'
check "modo TESTNET informado" 'grep -q "modo: TESTNET" <<<"$out"'
check "alerta Telegram enviado sem vazar token no log" 'grep -q api.telegram.org "$FD/curls" && ! grep -q SEGREDO <<<"$out"'

echo "$SHA_B" > "$FD/ref_origin_main"; echo unhealthy > "$FD/health_bbbbbbbbbbbb"
out="$("$ATU" --timeout 5 2>&1)"; rc=$?
check "deploy B doente → exit 1" '[ $rc -ne 0 ]'
check "rollback automático: tag atual continua A e A rodando" '[ "$(cat "$T/state/current_tag")" = aaaaaaaaaaaa ] && [ "$(cat "$FD/running_tag")" = aaaaaaaaaaaa ] && grep -q "ROLLBACK para a tag aaaaaaaaaaaa" <<<"$out"'

rm "$FD/health_bbbbbbbbbbbb"
out="$("$ATU" --timeout 5 2>&1)"; rc=$?
check "deploy B saudável → current=B previous=A" '[ $rc -eq 0 ] && [ "$(cat "$T/state/current_tag")" = bbbbbbbbbbbb ] && [ "$(cat "$T/state/previous_tag")" = aaaaaaaaaaaa ]'

out="$("$ATU" --rollback --timeout 5 2>&1)"; rc=$?
check "rollback manual volta para A" '[ $rc -eq 0 ] && [ "$(cat "$T/state/current_tag")" = aaaaaaaaaaaa ] && [ "$(cat "$T/state/previous_tag")" = bbbbbbbbbbbb ]'

echo cccccccccccc3333333333333333333333333333 > "$FD/ref_origin_main"
out="$(FAKE_BUILD_FAIL=1 "$ATU" --timeout 5 2>&1)"; rc=$?
check "build falho não derruba a versão atual" '[ $rc -ne 0 ] && [ "$(cat "$T/state/current_tag")" = aaaaaaaaaaaa ] && [ "$(cat "$FD/running_tag")" = aaaaaaaaaaaa ]'

echo "$SHA_B" > "$FD/ref_origin_main"
before="$(cat "$FD/running_tag")"; out="$("$ATU" --dry-run 2>&1)"; rc=$?
check "dry-run não altera nada" '[ $rc -eq 0 ] && [ "$(cat "$FD/running_tag")" = "$before" ]'

mkenv "GOLTRADER_API_TOKEN=curto"
out="$("$ATU" 2>&1)"; rc=$?
check "token curto é recusado" '[ $rc -ne 0 ] && grep -q "curto" <<<"$out"'

echo "-- portão de conta real --"
mkenv "BYBIT_TESTNET=false"
out="$("$ATU" 2>&1)"; rc=$?;               check "LIVE sem GOLTRADER_ALLOW_LIVE é recusado" '[ $rc -ne 0 ] && grep -q "GOLTRADER_ALLOW_LIVE" <<<"$out"'
mkenv $'BYBIT_TESTNET=false\nGOLTRADER_ALLOW_LIVE=true\nANALYZER_MODE=true'
out="$("$ATU" 2>&1)"; rc=$?;               check "LIVE com ANALYZER_MODE=true é recusado" '[ $rc -ne 0 ] && grep -q "ANALYZER_MODE" <<<"$out"'
mkenv $'BYBIT_TESTNET=false\nGOLTRADER_ALLOW_LIVE=true\nANALYZER_MODE=false'
out="$("$ATU" 2>&1)"; rc=$?;               check "LIVE sem --confirmar-live CONFIRMAR é recusado" '[ $rc -ne 0 ] && grep -q "confirmar-live" <<<"$out"'
out="$("$ATU" --confirmar-live CONFIRMAR 2>&1)"; rc=$?; check "LIVE sem GOLTRADER_LIVE_CONFIRMATION é recusado" '[ $rc -ne 0 ] && grep -q "GOLTRADER_LIVE_CONFIRMATION" <<<"$out"'
mkenv $'BYBIT_TESTNET=false\nGOLTRADER_ALLOW_LIVE=true\nANALYZER_MODE=false\nGOLTRADER_LIVE_CONFIRMATION=CONFIRMAR\nGOLTRADER_LIVE_HOST=outra-maquina'
out="$("$ATU" --confirmar-live CONFIRMAR 2>&1)"; rc=$?; check "LIVE em hostname diferente é recusado" '[ $rc -ne 0 ] && grep -q "GOLTRADER_LIVE_HOST" <<<"$out"'
mkenv "BYBIT_TESTNET=false
GOLTRADER_ALLOW_LIVE=true
ANALYZER_MODE=false
GOLTRADER_LIVE_CONFIRMATION=CONFIRMAR
GOLTRADER_LIVE_HOST=$(hostname)"
echo "goltrader-worker-1" > "$FD/ps"
out="$("$ATU" --confirmar-live CONFIRMAR 2>&1)"; rc=$?; check "LIVE com todas as travas passa (worker do próprio projeto não conta)" '[ $rc -eq 0 ] && grep -q "modo REAL" <<<"$out"'
echo "meu-goltrader-worker-9" > "$FD/ps"
out="$("$ATU" --confirmar-live CONFIRMAR 2>&1)"; rc=$?; check "LIVE com outro worker rodando é recusado" '[ $rc -ne 0 ] && grep -q "outro worker" <<<"$out"'
: > "$FD/ps"

echo "== watchdog.sh =="
mkenv; echo "$SHA_A" > "$FD/ref_origin_main"; rm -rf "$T/state"; mkdir -p "$T/state"; rm -f "$FD/running_tag" "$FD/curls" "$FD/restarts" "$FD/health_"*
"$ATU" --timeout 5 >/dev/null 2>&1
: > "$FD/curls"
"$WD" >/dev/null 2>&1; rc=$?
check "saudável: exit 0 e sem alerta" '[ $rc -eq 0 ] && ! grep -q telegram "$FD/curls"'
touch "$FD/api_down"
"$WD" >/dev/null 2>&1; "$WD" >/dev/null 2>&1
check "2 falhas: ainda não reinicia" '[ ! -s "$FD/restarts" ]'
"$WD" >/dev/null 2>&1
check "3ª falha: reinicia a api e alerta Telegram" '[ -s "$FD/restarts" ] && grep -q "sendMessage" "$FD/curls"'
n1="$(grep -c sendMessage "$FD/curls")"; "$WD" >/dev/null 2>&1
check "falha contínua não repete alerta (cooldown/dedupe)" '[ "$(grep -c sendMessage "$FD/curls")" = "$n1" ]'
rm "$FD/api_down"; "$WD" >/dev/null 2>&1; rc=$?
check "recuperação: exit 0 e alerta de RECUPERADO" '[ $rc -eq 0 ] && grep -q "RECUPERADO" <<<"$(grep -h text "$FD/curls")"'
echo "ok modo=testnet ws=True kill=True halt=None" > "$FD/hc_msg"
"$WD" >/dev/null 2>&1
check "kill switch acionado gera alerta" 'grep -q "KILL_SWITCH" <<<"$(grep -h text "$FD/curls")"'

echo; echo "resultado: $pass ok, $failn falha(s)"
[ "$failn" -eq 0 ]
