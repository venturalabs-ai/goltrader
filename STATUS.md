# STATUS — Goltrader Híbrido

Atualizado: 17/11/2026 06:11 BRT

## O que está sendo feito
Construção do **Goltrader híbrido** (notebook + VPS Hostinger 1925412) juntando: interface de fluxo do Tredye (reimplementada),
camada de corretoras/ferramentas inspirada/importada do OpenAlgo (AGPL-3.0) e motor/risco/Bybit do Goltrader. Testnet padrão;
conta real só com "CONFIRMAR"; kill switch mantido. **Sem GitHub Actions.**

## Feito
- [x] Código do Goltrader importado do notebook (sem `.env`, sem venv, sem `upstream_repos`); `.gitignore` protegendo segredos; varredura de segredos no histórico (limpo).
- [x] Repositório **público** sob **AGPL-3.0** (`LICENSE`, `NOTICE.md`).
- [x] Engenharia reversa do **Tredye** com o **REA 6.9.0** (6 páginas públicas + bundle): `docs/engenharia-reversa/tredye.md`, capturas e evidências.
- [x] Análise do **OpenAlgo**: `docs/engenharia-reversa/openalgo.md` (sem Bybit/B3; `deltaexchange` como molde cripto).
- [x] `docs/PLANO-HIBRIDO.md`: arquitetura, paleta, especificação do Pulse cripto, tarefas por fase.
- [x] Camada multi-corretora `brokers/` (interface comum, modos PAPER/TESTNET/LIVE, confirmação, kill switch, registro desligado por padrão) + 5 testes.

## Corretoras (adapters) — estado
| Corretora | Estado | API | Observação |
|---|---|---|---|
| Bybit | **PARCIAL** (ligada, Testnet) | V5 / pybit / ccxt | Falta E2E na Testnet para virar READY |
| Binance | STUB (desligado) | oficial / ccxt | testnet disponível |
| OKX | STUB (desligado) | v5 / ccxt | demo trading |
| Coinbase | STUB (desligado) | Advanced Trade / ccxt | sandbox limitado |
| Kraken | STUB (desligado) | REST/WS / ccxt | Spot e Futures separados |
| Interactive Brokers | STUB (desligado) | TWS/Gateway (ib_async) / Client Portal | paper; via IBKR dá acesso a B3 |
| Pepperstone | STUB (desligado) | cTrader Open API ou MT5 | MT5 só Windows |
| IQ Option | STUB (desligado) — **risco ToS/bloqueio** | sem API oficial (libs não oficiais) | manter desligado |

## Pendente
- Backend autenticado (hoje `api/server.py` tem CORS `*` e sem auth — **não expor na VPS assim**).
- Bybit READY (E2E Testnet), adapters ccxt, IBKR, cTrader.
- Pulse cripto (fluxo, breadth, humor) e frontend React com Sankey.
- Docker + deploy na VPS atrás do Traefik (build na VPS, deploy key somente-leitura).
- Paridade do notebook (mesmo código).

## Próximos passos
1. Fase 0 do plano (Docker, pre-commit/gitleaks, isolar código Windows).
2. Fase 1: API v1 com auth + módulo de risco + Bybit READY.
3. Fase 2/3: Pulse cripto + frontend. 4. Fase 4: deploy híbrido.

## Bloqueios / observações
- Dados reais do Tredye exigem login em corretora indiana — não feito (apenas modo exemplo público).
- Repositório `ventura-bots-backup` e DNS do golbot.com.br: intocados.
