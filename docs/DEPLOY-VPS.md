# Runbook — Goltrader 24/7 na VPS Hostinger (srv1925412)

Atualizado: 17/11/2026 08:32 BRT. Escopo: **Testnet primeiro**. Conta real só depois do "CONFIRMAR" do dono (seção 8).
Sem GitHub Actions: as imagens são construídas **na própria VPS** (mesmo padrão do golcrm e do golclaw).

## 1. Visão geral

```
Internet ──TLS──▶ Traefik (VPS, network_mode: host)
                    │  label traefik.docker.network=goltrader_default
                    ▼
               web (nginx, :8080)  ── /api/*  /ws/*  ──▶ api (FastAPI :8000, interno)
                                                          │      ▲
                          volume goltrader-state (/data/state)   │ SQLite, heartbeat, lease, KILL_SWITCH
                                                          ▼      │
                                                    worker (python -m execution.worker) ──▶ Bybit (REST + WS privado)
```

| Serviço | O que faz | Exposição |
|---|---|---|
| `web` | frontend compilado (Vite/React) + proxy `/api` e `/ws` | **só ele** sai pelo Traefik (HTTPS) |
| `api` | `backend/main.py:app` (token Bearer, CORS por env) | `127.0.0.1:18000` no host (watchdog); nunca público |
| `worker` | execução Bybit 24/7: ordens idempotentes, reconciliação, WS privado, kill switch, limites, lease | sem porta |

Todos: `restart: unless-stopped`, healthcheck, log `json-file` 10 MB × 5, usuário sem root, `read_only`, `cap_drop: ALL`.
Dados: volume nomeado `goltrader-state` (SQLite WAL, `heartbeat.json`, `lease.db`, `KILL_SWITCH`).

## 2. O que existe hoje na VPS (descoberta, 17/11/2026)
- VPS **1925412**, plano KVM 4 (4 vCPU, 16 GB, 200 GB), Ubuntu 24.04 **com Docker e Traefik** (template Hostinger), IPv4 `187.127.13.69`, hostname `srv1925412.hstgr.cloud`, estado *running*.
- O Traefik roda em `network_mode: host` com `providers.docker`; entrypoints `web` (80) e `websecure` (443); resolvedor de certificado **`letsencrypt`**. Cada projeto escolhe a rede por label: `golcrm-app` usa `traefik.docker.network=golcrm-app_default`, `golclaw` usa `golclaw_default` → o Goltrader usa **`goltrader_default`** (projeto Compose `goltrader`).
- Padrão de deploy dos outros apps: imagem `app:<sha12>` construída na VPS pelo projeto `vps-build`, `pull_policy: never`, troca de tag + Redeploy, log 10 MB × 5. Hostnames de preview no formato `<app>.srv1925412.hstgr.cloud`.
- Conferido pela API de leitura da Hostinger (`vps_docker_get golcrm-app`, `vps_virtual-machines_get`). A listagem geral (`vps_docker_list`) respondeu erro HTTP 500 no momento; nada foi alterado e **nada do Goltrader foi implantado**.

## 3. Pré-requisitos
1. PRs mergeados na `main`: **execução Bybit** (`feat/bybit-execucao-24x7`), **Pulse** (já na main) e **deploy** (este). Sem o PR de execução o worker não existe (`scripts/preflight.sh` avisa).
2. Domínio: padrão `goltrader.srv1925412.hstgr.cloud` (teste `dig +short goltrader.srv1925412.hstgr.cloud` → deve dar `187.127.13.69`). Se não resolver, crie um registro **A** do seu domínio para `187.127.13.69` e use-o em `GOLTRADER_DOMAIN`.
3. Chaves da **Testnet** Bybit (https://testnet.bybit.com), **sem permissão de saque**. Sem elas o worker sobe em modo ocioso (heartbeat ok, nenhuma ordem).
4. Acesso SSH root à VPS.

## 4. Primeiro deploy (Testnet)
```bash
ssh root@187.127.13.69
git clone https://github.com/venturalabs-ai/goltrader.git /opt/goltrader      # repositório público: sem deploy key
cd /opt/goltrader/deploy/hostinger-vps
cp .env.example .env && chmod 600 .env
openssl rand -base64 32                       # → cole em GOLTRADER_API_TOKEN no .env
nano .env                                     # GOLTRADER_DOMAIN, BYBIT_API_KEY/SECRET (Testnet). NÃO mude as travas.
./atualizar.sh --dry-run                      # valida .env, compose e arquivos do build
./atualizar.sh                                # build (tag = sha12), sobe, espera saúde; volta sozinho se falhar
sudo ./install-watchdog.sh                    # timer systemd de 1 em 1 minuto
```
Conferir:
```bash
./vps-compose.sh ps                                             # api, worker, web: healthy
curl -s http://127.0.0.1:18000/api/saude                        # {"status":"ok", "bybit_testnet": true, ...}
./vps-compose.sh exec worker python -m execution.cli status     # heartbeat do worker (modo testnet)
curl -sI https://SEU_DOMINIO | head -1                           # 200 com certificado válido
```
No painel (`https://SEU_DOMINIO`) informe o `GOLTRADER_API_TOKEN` na tela (fica só no `sessionStorage`).

## 5. Alternativa: pelo Docker Manager (hPanel)
1. Projeto one-shot `goltrader-build` com `deploy/hostinger-vps/goltrader-build.compose.yml`, variáveis `GOLTRADER_COMMIT` (sha completo) e `GOLTRADER_SHA` (12 primeiros) → constrói `goltrader-api:<sha12>` e `goltrader-web:<sha12>` (contexto HTTPS, repo público).
2. Projeto `goltrader` com `deploy/hostinger-vps/docker-compose.yml` e as variáveis do `.env` (incl. `GOLTRADER_TAG=<sha12>`). Trocar versão = trocar `GOLTRADER_TAG` + Redeploy; voltar = tag anterior.
Neste caminho não há rollback automático nem watchdog (use o SSH para isso).

## 6. Operação diária
| Tarefa | Comando (em `/opt/goltrader/deploy/hostinger-vps`) |
|---|---|
| Estado dos serviços | `./vps-compose.sh ps` |
| Logs | `./vps-compose.sh logs -f --tail 100 worker` (ou `api`, `web`) |
| Saúde do worker | `./vps-compose.sh exec worker python -m execution.healthcheck` |
| Status detalhado | `./vps-compose.sh exec worker python -m execution.cli status` |
| Reconciliar agora | `./vps-compose.sh exec worker python -m execution.cli reconcile` |
| Reiniciar | `./vps-compose.sh restart worker` |
| Watchdog | `journalctl -u goltrader-watchdog.service -n 50` · `systemctl list-timers goltrader-watchdog.timer` |

### Kill switch
- **Imediato**: `./vps-compose.sh exec worker python -m execution.cli kill "motivo"` → cria `KILL_SWITCH`, bloqueia ordens novas e fecha as posições lineares com ordens *reduce-only* (spot: só cancela ordens, não vende saldo).
- Também dispara sozinho ao estourar a **perda diária** (`GOLTRADER_MAX_DAILY_LOSS_USD`).
- Liberar: `... execution.cli clear-kill --confirm` (remove a trava; a perda diária continua travando até o dia seguinte UTC).
- ⚠️ O `POST /api/v1/kill_switch` da API (backend do Pulse) hoje age no estado em memória da API e **ainda não escreve o `KILL_SWITCH` do worker**. Para parar o worker use o comando acima ou crie o arquivo (`touch /var/lib/docker/volumes/goltrader-state/_data/KILL_SWITCH`). Integração listada em "Pendências".

### Backup do estado
```bash
mkdir -p /var/backups/goltrader
docker run --rm -v goltrader-state:/data:ro -v /var/backups/goltrader:/backup alpine \
  tar czf /backup/state-$(date +%F-%H%M).tar.gz -C /data .
```
Restaurar: pare o worker, extraia o tar de volta no volume e suba de novo (o worker reconcilia com a corretora ao iniciar).

## 7. Atualizar e voltar
```bash
cd /opt/goltrader/deploy/hostinger-vps
./atualizar.sh                  # origin/main        | ./atualizar.sh <branch|tag|sha>
./atualizar.sh --dry-run        # só valida
./atualizar.sh --rollback       # volta para a tag anterior (sem build)
```
O que o `atualizar.sh` faz: trava de deploy (`flock`, um por vez) → confere `.env` (token ≥ 24 caracteres, domínio, **portão de conta real**) → `git fetch` + checkout detached → `preflight` → **build** das duas imagens com tag = sha12 → `up -d` → espera `api`, `worker` e `web` ficarem *healthy*, confere a imagem em uso e o `/api/saude` → grava `current_tag`/`previous_tag` em `/var/lib/goltrader`. **Se qualquer passo da subida falhar, volta sozinho** para a tag anterior (e avisa por Telegram, se configurado). Build que falha não derruba a versão no ar. Mantém as 3 imagens mais novas.

## 8. Conta real (LIVE) — só depois do "CONFIRMAR"
Padrão do projeto: `BYBIT_TESTNET=true`, `ANALYZER_MODE=true`, `GOLTRADER_ALLOW_LIVE=false`. Para operar real **todas** estas condições precisam valer ao mesmo tempo (o `atualizar.sh` e o próprio worker recusam caso contrário):
1. `BYBIT_TESTNET=false`
2. `GOLTRADER_ALLOW_LIVE=true`
3. `ANALYZER_MODE=false`
4. `GOLTRADER_LIVE_CONFIRMATION=CONFIRMAR` no `.env` e `./atualizar.sh --confirmar-live CONFIRMAR`
5. `GOLTRADER_LIVE_HOST=$(hostname)` (só **esta** máquina pode operar real) e nenhum outro worker do Goltrader rodando nela
6. `BYBIT_REAL_API_KEY/SECRET` preenchidas **e** `BYBIT_API_KEY/SECRET` apontando para a chave real (a API do Pulse lê estas), chave **sem saque** e **restrita ao IP `187.127.13.69`** na Bybit

Antes disso: rodar ao menos alguns dias na Testnet, conferir reconciliação, kill switch e limites. **Uma instância só opera real**: o worker usa um *lease* em SQLite (um por volume) e o notebook é forçado a Testnet; entre máquinas diferentes a trava de verdade é a **lista de IPs da chave real na Bybit** (o notebook não consegue usá-la).

## 9. Watchdog e alertas
`watchdog.sh` (timer de 1 min): `GET /api/saude` + saúde dos 3 containers. Após **3 falhas seguidas** reinicia o que está ruim (cooldown de 10 min) e avisa; avisa também a recuperação e quando o **kill switch** ou a **trava de perda diária** são acionados. Alertas Telegram são opcionais (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` no `.env`; vazio = só log). Variáveis: `GOLTRADER_WATCHDOG_FAILS`, `GOLTRADER_WATCHDOG_COOLDOWN`. Remover: `sudo ./install-watchdog.sh --remover`.

## 10. Notebook (Windows) — `C:\Users\Ventura\goltrader`
Scripts em `scripts\windows\` (PowerShell 5.1+). **O notebook roda só Testnet** (as travas são forçadas pelo script).
```powershell
cd C:\Users\Ventura\goltrader\scripts\windows      # (ou baixe a pasta do repositório)
.\backup-goltrader.ps1                              # zip verificado em C:\Users\Ventura\goltrader_backups (mantém 10)
.\instalar-atualizar.ps1 -Confirmar                 # backup + git (adota a pasta) + venv + deps + frontend + .env de Testnet
.\rodar-testnet.ps1                                 # worker + API :8000 + painel :5173 (Testnet forçada)
.\rodar-testnet.ps1 -Status                         # saúde do worker   |   -Parar  para tudo
```
`-Confirmar` só é necessário na primeira vez, quando a pasta existe e ainda não é um repositório git: a adoção troca os arquivos rastreados pela versão do GitHub (o backup é feito antes e o script aborta se ele falhar). O `.env` existente nunca é sobrescrito. O backup inclui o `.env` (segredos) — fica só na máquina; `-ExcluirEnv` deixa-o de fora.

## 11. Segurança (resumo)
- Segredos só no `.env` da VPS (`chmod 600`, fora do Git). O repositório é público: o `preflight` aborta se `.env` estiver versionado; as imagens não copiam `.env` (`*.dockerignore`).
- A API nunca é exposta direto; só o `web` passa pelo Traefik (HTTPS, HSTS, `nosniff`, `frame-deny`). Rotas de escrita e WebSocket exigem `GOLTRADER_API_TOKEN`.
- Containers sem root, sistema de arquivos somente leitura, sem capabilities, `no-new-privileges`, limites de memória/CPU.
- Chave Bybit **sem saque**; real restrita ao IP da VPS.

## 12. Problemas comuns
| Sintoma | Causa provável / ação |
|---|---|
| `atualizar.sh`: "faltam arquivos para o build" | PR de execução ou do Pulse ainda não está na `main`; veja a lista do `preflight` |
| Certificado não emite | `GOLTRADER_DOMAIN` não aponta para `187.127.13.69` (ou porta 80 bloqueada); `docker logs` do Traefik |
| `web` reinicia | `api` não ficou *healthy* (`./vps-compose.sh logs api`) |
| Worker "ocioso (sem credenciais)" | faltam `BYBIT_API_KEY/SECRET` da Testnet no `.env` |
| Worker sai com "Outra instância já opera" | lease em uso (outro worker no mesmo volume); espere o TTL (30 s) ou pare o outro |
| `LIVE recusado: ...` | falta alguma das condições da seção 8 (é de propósito) |
| Painel sem dados | confira `GOLTRADER_DATA_SOURCE` e o token informado na tela |

## 13. Pendências / limites conhecidos
- **Nada foi implantado ainda** e não há E2E na Testnet (faltam as chaves). Bybit só vira *READY* depois disso.
- Integrar o botão/endpoint de kill switch da API ao `KILL_SWITCH` do worker (hoje são independentes).
- O worker executa ordens enfileiradas (`execution.cli submit` / tabela `commands`); a ligação do funil do Pulse a essa fila ainda não existe.
- Lease é por volume; entre máquinas a proteção é a restrição de IP da chave.
- Testes dos scripts: `bash scripts/tests/test-deploy-scripts.sh` (docker/git/curl falsos, sem rede). Validação estática: `shellcheck`, `docker compose config`, `hadolint`, parser do PowerShell.
