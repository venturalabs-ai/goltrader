#!/usr/bin/env bash
# Confere se o checkout tem tudo que as imagens precisam. Uso: scripts/preflight.sh [raiz-do-repo]
set -Eeuo pipefail
ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
missing=0
for f in backend/main.py backend/requirements.txt brokers/__init__.py core/safety.py execution/worker.py \
         frontend/package.json frontend/package-lock.json deploy/requirements-worker.txt \
         deploy/docker/Dockerfile.api deploy/docker/Dockerfile.web deploy/docker/nginx.conf \
         deploy/hostinger-vps/docker-compose.yml; do
  if [ ! -e "$ROOT/$f" ]; then echo "FALTA: $f"; missing=1; fi
done
if [ "$missing" = 1 ]; then
  echo "Dica: execution/, core/safety.py e brokers/bybit*.py vêm do PR feat/bybit-execucao-24x7; backend/ e frontend/ do PR do Pulse." >&2
  exit 1
fi
# segredos nunca no contexto de build
if git -C "$ROOT" ls-files --error-unmatch .env deploy/hostinger-vps/.env >/dev/null 2>&1; then
  echo ".env versionado no Git: remova antes de seguir" >&2; exit 1
fi
echo "preflight OK"
