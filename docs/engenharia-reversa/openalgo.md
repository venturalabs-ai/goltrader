# Análise — OpenAlgo

Repositório analisado: https://github.com/marketcalls/openalgo (clone raso, commit `0d66bdf`, v2.0.3.4, 17/11/2026).
Licença: **GNU AGPL-3.0**. Análise estática do código (nada executado).

## 1. Arquitetura
- **Backend Flask (Python ≥ 3.12)** + **frontend React 19** (Vite, Tailwind, shadcn/Radix, zustand, TanStack Query,
  socket.io, plotly, `@xyflow/react` para o editor Flow, CodeMirror). Bancos SQLite/SQLAlchemy (6 stores).
- Camadas: `blueprints/` (rotas/UI) → `restx_api/` (REST `/api/v1/*`, contrato único) → `services/` (regra de negócio,
  retorno `(sucesso, resposta, status)`) → `broker/<nome>/` (plugin por corretora) ; `database/`, `utils/`.
- **Plugins de corretora** (`broker/*`, 39 plugins): cada um traz `plugin.json` (exchanges suportadas, `broker_type`),
  `api/{auth_api,order_api,data,funds,margin_api}.py`, `mapping/` (tradução de símbolos/ordens), `database/master_contract_db.py`
  (download do contrato mestre de símbolos), `streaming/*_adapter.py` (WebSocket). Descoberta automática por
  `utils/plugin_loader.py`; guia: `docs/broker-integration-guide.md`.
- **Streaming**: `websocket_proxy/` (servidor WS + ZeroMQ, pool de conexões, `base_adapter.py`) normaliza ticks de todos os brokers.
- **Execução**: `services/place_order_service`, `place_smart_order_service` (posição alvo), `basket`, `split_order`, `options_order`,
  GTT, `order_router_service`, `pending_order_execution_service`; proteções `utils/smart_order_guard.py` (locks por símbolo,
  cache de posição), `broker_backpressure`, `qty_freeze`, rate limiter por broker.
- **Analyzer Mode / Sandbox** (`sandbox/`): ordens simuladas com motor de execução, fundos, posições, square-off — equivale ao nosso paper trading.
- **Risco** (`services/risk/`): núcleo **puro, sem I/O** (stop, alvo, trailing, limites agregados, lock profit) com vetores
  de teste ("golden vectors") — modelo excelente para o risco do Goltrader.
- **UI/produtos**: Python Strategy Host (`/python`), Flow no-code (`/flow`), AI Agent (`/agent`, ordens só com aprovação),
  Options & Portfolio Suite (`/tools`: option chain, OI tracker, max pain, GEX, IV smile, vol surface, **Sector Rotation**,
  backtester), Scalping Terminal, MCP server (`mcp/`), alertas Telegram/WhatsApp, TradingView/Chartink webhooks.

## 2. Corretoras
Lista (39): 5paisa, AliceBlue, AngelOne, Arrow, Compositedge, Definedge, **Delta Exchange (cripto/derivativos)**, Dhan (+Sandbox),
Firstock, Flattrade, Fyers, Groww, HDFC Sec/Sky, IIFL, IndMoney, Kotak, Motilal, mStock, Paytm, Shoonya, Upstox, **Zerodha**, etc.
- **Bybit: NÃO existe.** **Binance/OKX/Coinbase/Kraken: NÃO.** **Interactive Brokers: NÃO.** **MT5/cTrader (Pepperstone): não é plugin**
  (README cita compatibilidade MetaTrader como *cliente* da API REST do OpenAlgo). **IQ Option: NÃO.**
- **B3/Brasil: NÃO** (foco NSE/BSE/MCX da Índia). Para B3 seria preciso um plugin novo (ex.: via IBKR, Profit/MetaTrader 5 de corretora BR, ou API de corretora brasileira).
- Único exemplo cripto: `broker/deltaexchange` (≈3,6 mil linhas: auth, ordens, dados, margem, rate limiter, streaming) — **bom molde**
  para escrever o adapter Bybit no formato OpenAlgo.

## 3. O que reaproveitar (e como)
| Item | Onde | Uso no Goltrader | Esforço |
|---|---|---|---|
| Padrão de plugin de corretora + guia | `docs/broker-integration-guide.md`, `broker/deltaexchange` | Molde dos adapters (`brokers/`) | baixo (ideia) |
| Núcleo de risco puro + vetores de teste | `services/risk/` | Base do risco/kill switch/trailing | médio (copiar+adaptar) |
| Sandbox / Analyzer Mode | `sandbox/` | Paper trading unificado | médio |
| Smart order guard, rate limiter, backpressure | `utils/smart_order_guard.py`, `broker/*/api/rate_limiter.py` | Robustez de execução | médio |
| WebSocket proxy normalizado | `websocket_proxy/` | Stream único de ticks (Bybit WS) | alto |
| Ferramentas de opções (OI, max pain, GEX, IV smile, vol surface) | `services/*oi*`, `*gex*`, `vol_surface_service` | Opções cripto (Deribit/Bybit options) | médio |
| Sector Rotation (RRG: RS-Ratio/RS-Momentum) | `services/sector_rotation_service.py` | Rotação entre categorias cripto | baixo/médio |
| Editor Flow (@xyflow), Strategy Host | `blueprints/flow.py`, `services/flow_*`, `openscript_host` | Fase 3+ (opcional) | alto |
| MCP server | `mcp/` | Controle por agentes | baixo |
| Frontend React/shadcn | `frontend/` | Referência de UI (usaremos o nosso tema) | médio |

## 4. Implicações da AGPL-3.0
- **Uso pessoal/privado** (notebook, VPS só sua): sem obrigação de publicar.
- **Código copiado/adaptado do OpenAlgo torna o derivado AGPL**: manter licença, avisos e histórico de modificações.
- **Cláusula de rede (§13)**: se o Goltrader com código OpenAlgo for **acessível a terceiros pela rede** (ex.: painel público
  na VPS), é preciso oferecer o **código-fonte completo** a esses usuários. Por isso o repositório do Goltrader foi
  publicado como **público sob AGPL-3.0** (decisão do Wemerson) — isso atende o requisito.
- Não herdar marcas/logos "OpenAlgo". Bibliotecas de terceiros mantêm suas licenças (Flask BSD, React MIT, etc.).
- Cuidado com **dados/segredos**: nunca commitar `.env`, chaves, tokens (o `.gitignore` protege).
