#!/usr/bin/env bash
# Atalho: docker compose do projeto goltrader com a tag atual (gravada pelo atualizar.sh).
#   ./vps-compose.sh ps | logs -f worker | restart worker | exec worker python -m execution.cli status
set -Eeuo pipefail
LOG_TAG=vps-compose
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
[ -n "${GOLTRADER_TAG_CALL:-$(current_tag)}" ] || die "sem tag atual; rode ./atualizar.sh primeiro (ou exporte GOLTRADER_TAG_CALL=<sha12>)"
dc "$@"
