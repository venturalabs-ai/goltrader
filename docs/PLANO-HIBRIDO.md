# Plano do Goltrader Híbrido

Versão 1 — 17/11/2026 (BRT). Meta: unir **o melhor de 3 apps** — (1) interface de fluxo de mercado do **Tredye**,
(2) camada de corretoras/execução/ferramentas do **OpenAlgo**, (3) motor, risco e Bybit do **Goltrader** atual —
rodando **no notebook e na VPS Hostinger 1925412** com o mesmo código. **Testnet por padrão; conta real só com
confirmação explícita ("CONFIRMAR"); kill switch sempre ativo.**

Referências: `engenharia-reversa/tredye.md`, `engenharia-reversa/openalgo.md`, `STATUS.md`.

## 1. Princípios
1. **Segurança antes de recursos**: Testnet/paper padrão; LIVE exige `CONFIRMAR` + kill switch checado a cada ordem.
2. **Um código, dois ambientes**: mesma imagem Docker/venv no notebook (Windows) e na VPS (Linux).
3. **Reimplementar, não copiar o Tredye**; **reaproveitar o OpenAlgo** sob AGPL-3.0 (repo público + `LICENSE` + `NOTICE.md`).
4. **Sem GitHub Actions** (sem minutos extras): build na VPS a partir do repo (padrão já usado em golcrm/golclaw) ou local.
5. **Zero segredos no Git**: `.env` fora do repo; chaves só em variáveis de ambiente/volume da VPS.
6. Cada adapter de corretora é **desligado por padrão** (`GT_BROKERS_ENABLED=bybit`).

## 2. Arquitetura alvo
```
                       ┌────────────────────────── Navegador (notebook/celular) ─────────────────────────┐
                       │  Frontend web (React+Vite+TS, tema Goltrader, fonte Inter)                        │
                       │  Telas: Pulse/Fluxo (Sankey), Mercados, Opções/Liquidez, Força, Radar, Operar,    │
                       │         Estratégias/Backtest, Risco & Kill switch, Corretoras                     │
                       └───────────────▲──────────────────────────────▲────────────────────────────────────┘
                                REST/JSON (/api/v1)           WebSocket (/ws: ticks, fluxo, ordens)
                       ┌───────────────┴──────────────────────────────┴────────────────────────────────────┐
  Traefik (VPS) ──TLS─▶│  Backend FastAPI (api/)                                                           │
                       │  auth (token/usuário único) · risco/kill switch · roteador de ordens · agendador  │
                       ├───────────────┬──────────────────┬───────────────────┬────────────────────────────┤
                       │ Motor Goltrader│ Pulse Engine     │ Brokers (adapter) │ Persistência (SQLite→PG)   │
                       │ 10 estratégias │ fluxo/breadth/   │ bybit (ready)     │ ordens, trades, logs,      │
                       │ backtest/WF    │ humor/strength/  │ binance okx       │ snapshots de fluxo,        │
                       │ autotrader     │ radar (cripto)   │ coinbase kraken   │ config de risco            │
                       │                │                  │ ibkr pepperstone  │                            │
                       │                │                  │ iqoption (risco)  │                            │
                       └────────────────┴──────────────────┴───────────────────┴────────────────────────────┘
        Streamlit atual (dashboard.py) permanece como "modo legado" até a paridade do novo frontend.
```
- **Backend**: FastAPI (já existe `api/server.py`) + motor Python atual. Worker do autotrader separado (processo/serviço).
- **Frontend**: React + Vite + TypeScript, **Sankey em `<canvas>` próprio** (curvas Bézier, partículas), gráficos de candle com
  **lightweight-charts** (Apache-2.0), tabelas/painéis com Tailwind. Servido pelo próprio FastAPI (estático) → 1 container.
- **Dados de mercado cripto** (Pulse): Bybit V5 público (`/v5/market/tickers`, kline, `publicTrade` WS, instruments, open
  interest, funding) — sem chave. Categorias (setores) mantidas em `data/categorias_cripto.json` (curadoria própria).
- **B3 (se viável)**: fase posterior, via **IBKR** (ações BR) ou MT5 de corretora BR; fonte de dados de referência `brapi.dev`/yfinance
  apenas para leitura. Não é prioridade.
- **Deploy VPS**: container `goltrader` atrás do **Traefik existente** (labels, domínio dedicado, TLS), healthcheck `/api/saude`,
  logs limitados, restart policy; build na VPS com **deploy key somente-leitura** (mesmo fluxo de golcrm/golclaw; ver runbook
  `deploy/hostinger-vps/` desses repos). Serializar deploys (checar lock). Volume persistente para `state/`, `logs/`, DB.
- **Notebook**: mesmo código via `docker compose up` ou `venv`; `.bat` atuais continuam até migrar. Código com `ctypes.windll`
  (`autostate.py`) deve ser isolado por `sys.platform` para rodar em Linux.

## 3. Identidade visual
Fundo `#121318` · painéis `#191C23` · campos `#1A1C23` · superfícies `#212A34` · apoio/texto secundário `#646C78` ·
destaque **turquesa `#40D7DD`** · fonte **Inter**. Positivo/negativo: verde `#2ECC8F` / vermelho `#FF5C6C` (sugestão,
com contraste AA sobre `#191C23`). Raio 12–14 px, bordas 1 px `#212A34`, números tabulares. Mapear em tokens CSS
(`--bg, --panel, --field, --surface, --muted, --accent`). Modo escuro único.

## 4. Pulse cripto — especificação (reimplementação própria do conceito Tredye)
- **Universo**: top N (50–150) pares USDT por turnover 24h na Bybit (spot e/ou perp linear), classificados em ~15–20 categorias.
- **Fluxo por ativo** (USDT) por janela (5m/1h/24h): `f = turnover × (2·buy_ratio − 1)`, onde `buy_ratio` = volume de taker
  comprador / total (agregado de `publicTrade`; fallback: CLV do candle `(C−L)/(H−L)`). `fin=Σ f>0`, `fout=Σ f<0`.
- **Fluxo por categoria** = Σ f dos ativos; **Sankey**: esquerda = categorias (entrada/saída), centro = barras INFLOW/OUTFLOW,
  direita = top 8–10 ativos que mais entram/saem. Espessura ∝ |f|, velocidade ∝ volume relativo (`rv` = turnover / mediana 20 períodos),
  brilho = surto (`rv`>3 ou z-score).
- **Breadth** = avançam:recuam (24h) e % acima da VWAP/EMA20. **Volatilidade**: ATR% (BTC/ETH), percentil 1 ano; **DVOL/IV** se disponível.
- **Humor 0–100** = `50 + 50·clip(0.35·flow + 0.25·breadth + 0.20·btc_trend + 0.20·(−vol_z))`, pesos configuráveis, rótulos
  (Medo extremo <20, Cautela <40, Neutro <60, Construtivo <80, Euforia). Manchete textual gerada ("Dinheiro saiu de X e foi para Y").
- **Drill-down** categoria → ativo → painel de **order flow** (delta de taker, CVD, livro L2, OI e funding p/ perps, liquidações).
- **Outras telas**: Opções (Bybit/Deribit: cadeia, PCR, max pain, IV smile — adaptar `services/*oi*` do OpenAlgo), Liquidez
  (candles + perfil de volume + VWAP ancorada + muros de OI), Força (relativa a BTC, normalizada por ADR), Radar (8 sinais → −10..+10).
- Refresh sugerido: 5–15 s para fluxo ao vivo; snapshots gravados a cada 1 min para histórico/backtest.

## 5. Camada de corretoras (adapter multi-exchange)
Implementada em `brokers/`: interface comum `BrokerAdapter` (`klines, ticker, balance, positions, place_order, cancel_all`),
`Mode` (PAPER/TESTNET/LIVE), `require_live_confirmation("CONFIRMAR")`, kill switch, registro com **tudo desligado
exceto Bybit** (`GT_BROKERS_ENABLED`). Testes em `tests/test_brokers.py` (5 passando).

| Corretora | Prontidão | API oficial / SDK | Reaproveitável do OpenAlgo | Tarefas concretas |
|---|---|---|---|---|
| **Bybit** (foco) | PARCIAL → READY | API V5 (`pybit`, `ccxt.bybit`); Testnet `api-testnet.bybit.com` | Molde `broker/deltaexchange` (auth, ordens, rate limiter, WS); `sandbox/`; `smart_order_guard` | Testes E2E na Testnet (spot + linear): market/limit, cancel, posições, saldo; qty/tick via `instruments-info`; rate limit; WS privado (ordens/execuções); reconciliar posição antes de ordem; log de auditoria; promover para READY; só então liberar LIVE com `CONFIRMAR` |
| **Binance** | STUB | API oficial Spot/USDⓈ-M; `ccxt.binance`; testnet `testnet.binance.vision` / `testnet.binancefuture.com` | Padrão de plugin/mapping de símbolos | Implementar via ccxt (market data → ordens → saldo); testnet; checar disponibilidade/regras para residentes no Brasil; símbolos/filters (LOT_SIZE, NOTIONAL) |
| **OKX** | STUB | API v5; `ccxt.okx`; demo trading (header `x-simulated-trading: 1`) | idem | ccxt + passphrase; modo demo; contas unificadas; mapping `instId` |
| **Coinbase** | STUB | Advanced Trade API (chaves CDP/JWT); `ccxt.coinbase` | idem | Auth JWT (ES256); só spot; sandbox limitado → validar com ordens mínimas controladas ou só paper |
| **Kraken** | STUB | REST/WS v2; `ccxt.kraken`; Futures demo `demo-futures.kraken.com` | idem | Spot e Futures são APIs/chaves separadas; nonce; mapping de pares (XBT) |
| **Interactive Brokers** | STUB | **TWS API / IB Gateway** (`ib_async`) ou Client Portal Web API | Padrões de master contract/símbolos (`database/symbol`, `token_db`) | IB Gateway em Docker (paper, porta 4002) na VPS ou TWS no notebook; contratos (conId); permissões de dados; ações BR/B3, opções, futuros; ordens com `whatIf` antes |
| **Pepperstone** | STUB | **cTrader Open API** (OAuth2, protobuf) *ou* **MetaTrader 5** (`MetaTrader5`, só Windows) | OpenAlgo é *cliente* MT5 da própria API (não há plugin) | Preferir cTrader Open API (roda em Linux/VPS, conta demo); alternativa MT5 apenas no notebook Windows; símbolos FX/CFD; lote/margem; sem custódia de senha no código |
| **IQ Option** | STUB — **RISCO** | **Sem API oficial pública**; só libs não oficiais (ex.: `iqoptionapi`) | Nada | **Manter desligado.** Violação possível de ToS e risco de bloqueio da conta; API instável. Se o usuário insistir: só conta PRACTICE, aviso explícito, isolado em processo separado, sem LIVE por padrão |

Notas: (a) o motor atual é de **opções binárias simuladas (CALL/PUT, expiração em candles)** — Bybit não tem binárias; o adapter
Bybit executa spot/perp reais e o motor deve evoluir para sinais direcionais com stop/alvo; IQ Option seria o único com binárias.
(b) OpenAlgo **não tem** Bybit/Binance/OKX/Coinbase/Kraken/IBKR/Pepperstone/IQ Option nem B3: tudo isso é trabalho novo, usando o
OpenAlgo como padrão de arquitetura (e `deltaexchange` como exemplo cripto).

## 6. Segurança e risco (obrigatório antes de qualquer LIVE)
- Autenticação no backend (hoje `api/server.py` tem **CORS `*` e nenhuma autenticação** → corrigir antes de expor na VPS): token/usuário único,
  CORS restrito ao domínio, rate limit, HTTPS via Traefik, bind interno.
- Kill switch: arquivo/flag **+** endpoint **+** botão; bloqueia novas ordens, cancela abertas, opcional fecha posições.
- Limites: stake máx. %, perda diária máx., posições abertas máx., tamanho mín/máx por símbolo, alavancagem máx.
- LIVE: confirmação textual "CONFIRMAR" por sessão, banner vermelho, log de auditoria imutável de cada ordem.
- Segredos: `.env` fora do Git; varredura (gitleaks) antes de cada push; chaves da API da corretora **sem permissão de saque**, com IP whitelist (IP da VPS).
- Nenhuma ordem real no CI/testes automatizados; testes usam Testnet/paper.

## 7. Lista de tarefas para agente de código (ordem de execução)
**Fase 0 — Base (1 dia)**
- [ ] T0.1 Ajustar `requirements.txt` (fixar versões) e criar `pyproject`/`Makefile` (`make test|run|lint`).
- [ ] T0.2 Isolar código Windows (`autostate.py`) por plataforma; remover `.bat` do caminho crítico (manter em `scripts/windows/`).
- [ ] T0.3 `Dockerfile` (python:3.12-slim, usuário não-root) + `docker-compose.yml` (serviços `api`, `worker`; volume `state/`) com labels Traefik **comentadas**; `/api/saude`.
- [ ] T0.4 Pre-commit com gitleaks/`detect-secrets`; testes (`pytest`) rodando localmente.

**Fase 1 — Backend e corretoras (2–3 dias)**
- [ ] T1.1 Módulo de risco único (`risk/`) portando o desenho puro/sem I/O de `services/risk` do OpenAlgo (AGPL, registrar em `NOTICE.md`), com vetores de teste; usado por autotrader e API.
- [ ] T1.2 API v1: `/api/v1/brokers` (catálogo), `/mode`, `/orders`, `/positions`, `/balance`, `/kill`, `/risk`; autenticação por token; CORS restrito; logs de auditoria.
- [ ] T1.3 Bybit READY: testes E2E Testnet (spot+linear), WS privado, `instruments-info` (tick/qty), reconciliação; fluxo LIVE com `CONFIRMAR`.
- [ ] T1.4 Adapters via `ccxt`: Binance, OKX, Coinbase, Kraken (mercado → conta → ordens) com testnet/demo; cada um com testes `@pytest.mark.integration` (desligados por padrão).
- [ ] T1.5 IBKR (`ib_async`, paper) e Pepperstone (cTrader Open API demo): leitura de dados/conta primeiro, ordens depois.
- [ ] T1.6 IQ Option: manter stub; documentar aviso de ToS na UI (sem implementação por padrão).
- [ ] T1.7 Persistência (SQLite → Postgres opcional): ordens, execuções, snapshots de fluxo.

**Fase 2 — Pulse cripto (3–4 dias)**
- [ ] T2.1 `pulse/universe.py` (top N por turnover + categorias em `data/categorias_cripto.json`).
- [ ] T2.2 `pulse/flow.py`: fluxo por ativo/categoria (taker buy/sell via `publicTrade` WS; fallback CLV), breadth, vol, humor, manchete; cache e snapshots 1 min.
- [ ] T2.3 Endpoints `/api/v1/pulse`, `/flow`, `/strength`, `/radar`, `/liquidity/{sym}`, `/options/{sym}` + WS `/ws/flow`.
- [ ] T2.4 Testes com fixtures gravadas (formato no estilo de `rea-evidencias/estrutura-json-amostras.json`).

**Fase 3 — Frontend (4–5 dias)**
- [ ] T3.1 Scaffold React+Vite+TS+Tailwind, tokens da paleta, fonte Inter, layout (header, abas, command palette).
- [ ] T3.2 Componente `SankeyCanvas` (categorias → barras IN/OUT → top ativos; partículas, brilho de surto; acessível com `aria-label`).
- [ ] T3.3 Telas Pulse (KPIs, humor, breadth, VIX/vol, fastest movers), Strength, Radar (8 eixos), Liquidez (lightweight-charts + perfil de volume), Opções.
- [ ] T3.4 Tela **Operar**: seleção de corretora/modo, banner TESTNET/REAL, ticket de ordem, posições, kill switch sempre visível, confirmação "CONFIRMAR".
- [ ] T3.5 Tela Estratégias/Backtest (reusar motor atual) e Risco.
- [ ] T3.6 E2E (Playwright) em Testnet/paper; Lighthouse/acessibilidade.

**Fase 4 — Deploy híbrido (1–2 dias)**
- [ ] T4.1 VPS: criar projeto Docker `goltrader` atrás do Traefik, build na VPS via deploy key somente-leitura (sem Actions), healthcheck, logs 10 MB×5, restart, volume.
- [ ] T4.2 Domínio/subdomínio + TLS; variáveis (chaves Testnet) só no ambiente da VPS; **Testnet por padrão**.
- [ ] T4.3 Notebook: `docker compose up` (mesmo código); sincronização por `git pull`; documentar runbook.
- [ ] T4.4 Observabilidade: logs estruturados, alerta Telegram opcional de kill switch/erros; backup do volume.

**Fase 5 — Extras**
- [ ] T5.1 B3 via IBKR/MT5-BR; T5.2 Flow no-code (inspirado no OpenAlgo); T5.3 MCP server; T5.4 rotação setorial (RRG) por categoria.

## 8. Critérios de aceite do MVP
Bybit Testnet operando ponta a ponta (ordem → posição → cancelamento) pela UI nova; Sankey/humor/breadth ao vivo; kill switch
testado (bloqueia e cancela); LIVE bloqueado sem "CONFIRMAR"; mesmo container rodando no notebook e na VPS; nenhum segredo no
repositório; testes verdes; licença AGPL e avisos presentes.
