#!/usr/bin/env bash
# Instala/atualiza o watchdog como systemd timer (a cada 1 min). Rode como root na VPS:  sudo ./install-watchdog.sh
# Desinstalar: sudo ./install-watchdog.sh --remover
set -Eeuo pipefail
LOG_TAG=install-watchdog
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
[ "$(id -u)" -eq 0 ] || die "rode como root (sudo)"
UNIT_DIR=/etc/systemd/system
if [ "${1:-}" = "--remover" ]; then
  systemctl disable --now goltrader-watchdog.timer 2>/dev/null || true
  rm -f "$UNIT_DIR/goltrader-watchdog.service" "$UNIT_DIR/goltrader-watchdog.timer"
  systemctl daemon-reload; log "watchdog removido"; exit 0
fi
mkdir -p "$STATE_DIR"
chmod +x "$COMPOSE_DIR/watchdog.sh" "$COMPOSE_DIR/atualizar.sh" "$COMPOSE_DIR/vps-compose.sh"
sed "s#@COMPOSE_DIR@#$COMPOSE_DIR#g; s#@STATE_DIR@#$STATE_DIR#g" "$COMPOSE_DIR/systemd/goltrader-watchdog.service" > "$UNIT_DIR/goltrader-watchdog.service"
install -m 0644 "$COMPOSE_DIR/systemd/goltrader-watchdog.timer" "$UNIT_DIR/goltrader-watchdog.timer"
systemctl daemon-reload
systemctl enable --now goltrader-watchdog.timer
systemctl list-timers goltrader-watchdog.timer --no-pager || true
log "watchdog instalado (logs: journalctl -u goltrader-watchdog.service)"
