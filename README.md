# Goltrader (híbrido)

Plataforma de trading em Python: motor com 10 estratégias, backtest/walk-forward, autotrader, risco e kill switch,
integração Bybit (Testnet por padrão), painel Streamlit — em evolução para um **híbrido** com interface de fluxo de mercado
(estilo Market Pulse), camada multi-corretora (adapters) e deploy no notebook + VPS.

- Status e próximos passos: [`STATUS.md`](STATUS.md)
- Plano/arquitetura: [`docs/PLANO-HIBRIDO.md`](docs/PLANO-HIBRIDO.md)
- Engenharia reversa: [`docs/engenharia-reversa/`](docs/engenharia-reversa/)
- Licença: **AGPL-3.0** ([`LICENSE`](LICENSE), [`NOTICE.md`](NOTICE.md))

> Aviso: operar mercados envolve risco de perda. Testnet/paper por padrão; conta real exige confirmação explícita. Não é recomendação de investimento.

Configuração: copie `.env.example` para `.env` e preencha **só localmente** (o `.env` é ignorado pelo Git).

---

## Pulse: backend assíncrono + frontend React (`backend/`, `frontend/`)

Interface de fluxo (estilo Market Pulse, reimplementada) + suíte de opções, sobre um backend FastAPI com WebSocket.

```
backend/            main.py (API + WS /ws/pulse) · engine.py (loop único) · funnel_engine.py (algoritmos puros)
                    options_engine.py (Black-Scholes, GEX, gamma flip, IV smile, max pain) · news_scanner.py (RSS + calendário)
                    feeds.py (SimulatedFeed / BybitFeed) · execution.py (risco + execução) · connectors/{bybit,iqoption}_async.py
frontend/           Vite + React + TS + Tailwind · FlowChart · CommandBar (Ctrl+K, relógio de bolsas, SANDBOX/LIVE) · OptionsSuite
```

### Rodar no notebook (Windows, PowerShell)

```powershell
# 1) backend
cd C:\Users\Ventura\goltrader\backend
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env        # edite SÓ no notebook; o .env é ignorado pelo Git
# gere um token:  python -c "import secrets; print(secrets.token_urlsafe(32))"  -> GOLTRADER_API_TOKEN no .env
uvicorn main:app --host 127.0.0.1 --port 8000

# 2) frontend (outro terminal)
cd C:\Users\Ventura\goltrader\frontend
npm install
npm run dev                   # http://localhost:5173  (o proxy do Vite encaminha /api e /ws para 127.0.0.1:8000)
```

Na tela, cole o token no campo "token da API" (fica só no `sessionStorage`). Testes: `cd backend && pytest -q`. Build: `cd frontend && npm run build`.

### Segurança (padrões)
- **Testnet/paper por padrão.** `ANALYZER_MODE=true`; funil autônomo **sempre inicia desligado**.
- **LIVE** exige `GOLTRADER_ALLOW_LIVE=true` **e** `POST /api/v1/toggle_mode` com `{"confirm":"CONFIRMAR"}`; senão `403`. Mesmo em LIVE, Bybit usa a **Testnet** salvo `BYBIT_TESTNET=false`; IQ Option usa `PRACTICE` salvo `IQ_BALANCE_MODE=REAL`.
- **Auth:** todo `POST /api/v1/*` e o WebSocket (`?token=`) exigem `GOLTRADER_API_TOKEN`. Sem token configurado o servidor devolve `503` nas rotas que alteram estado e só serve o WS a loopback. CORS só para `GOLTRADER_CORS_ORIGINS` (nunca `*`). Não exponha na internet sem HTTPS (o token do WS vai na URL).
- **Kill switch** (`POST /api/v1/kill_switch`): pausa o funil, volta ao paper, bloqueia novas ordens e (se estava em LIVE com chaves) tenta cancelar ordens abertas em perpétuos USDT. **Não fecha posições** (TODO). Limites: `GOLTRADER_MAX_ORDERS_PER_MIN`, `GOLTRADER_MAX_DAILY_LOSS_USD` (o PnL realizado real ainda **não está ligado** ao limite — TODO `closed-pnl`), `GOLTRADER_MAX_LEVERAGE`.
- Chaves **só** em variáveis de ambiente / `.env` local. Saúde: `GET /api/saude`.

### O que é simulado × real
| Item | `GOLTRADER_DATA_SOURCE=simulado` (padrão) | `=bybit` |
|---|---|---|
| Preço, velas, book, fluxo, OI, funding | **SIMULADO** (badge na UI e `data_source` no payload) | **Real** (WS/REST públicos da Bybit mainnet, sem chaves); book cobre só a profundidade recebida (`range_pct`, ~±0,05% no BTC, não ±0,5%) |
| Cadeia de opções (IV/OI/gregas) | **SIMULADA** (execução de estruturas desabilitada) | **Real** (`/v5/market/tickers?category=option`) |
| Funil autônomo | **sempre paper** | paper no analyzer; ordens reais só em LIVE |
| Notícias | RSS real (CoinDesk, Cointelegraph, Investing BR); sentimento por palavras-chave (heurística) | idem |
| Calendário econômico | **EXEMPLO** (sem datas/valores) — plugar provedor com `set_calendar_provider()`; nunca alimenta o funil | idem |
| Execução | Bybit V5 (linear/spot/option) via REST assinado; **IQ Option**: só `IQ_BINARY`; `IQ_DIGITAL` ainda não implementado | idem |

> **IQ Option não tem API oficial.** O conector usa o websocket interno da plataforma (engenharia reversa de terceiros): pode quebrar sem aviso, pode violar os Termos de Uso e há risco de bloqueio de conta. Mantenha desligado salvo decisão consciente (conta PRACTICE).

Limitações conhecidas: OBs são calculados em velas de 1m (não há multi-timeframe 5m/1m ainda); GEX usa a premissa clássica "dealers long calls/short puts"; vol realizada vem de retornos de 1m das velas disponíveis; OI do pulse é variação em janela curta (campo `oi_window_s`), não 24h.

### Risco institucional + portão de confiança (`backend/risk/`, `backend/nod_core/`)
- **`NodTrustGate`** é o **único** ponto de aprovação de ordens (cesta manual, estrutura de opções, funil autônomo): kill switch, circuit breaker, perda diária, ordens/min, alavancagem, preço de referência, venues permitidas, LIVE (`GOLTRADER_ALLOW_LIVE` + saldo real lido + teto de margem). **IQ Option fica desabilitada** (`GOLTRADER_ENABLE_IQ=false`). Cada decisão vai para o *trust ledger* (visível no painel).
- **`InstitutionalRiskManager`**: Kelly fracionado (`f* = p − (1−p)/b`, × `GOLTRADER_KELLY_FRACTION`, teto `GOLTRADER_MAX_RISK_PER_TRADE_PCT`), breakeven (a +1R), trailing (`GOLTRADER_TRAIL_PCT`) e **circuit breaker** de drawdown diário (`GOLTRADER_MAX_DAILY_DRAWDOWN_PCT`).
  - **Equity:** saldo real da Bybit (testnet por padrão) quando há chaves; senão `GOLTRADER_PAPER_EQUITY`, **rotulado PAPER**. Sem nenhum dos dois: indefinido e o dimensionamento recusa. LIVE nunca usa o paper equity.
  - **Win rate / payoff:** começam **neutros (0,5 / 1,0)** e só passam a vir do histórico de trades fechados (persistido em `backend/state/trades.jsonl`, fora do Git) após `GOLTRADER_KELLY_MIN_TRADES`; trades de dados SIMULADOS não contam. Enquanto neutro, o funil usa risco mínimo **BOOTSTRAP** (`GOLTRADER_BOOTSTRAP_RISK_PCT`).
  - **PnL não realizado** = `margem × move% × alavancagem` com a alavancagem real de cada posição (`size_usd` = margem).
  - **Breaker:** ao disparar, fecha posições (**LIVE: ordem a mercado reduce-only na Bybit** para tudo que a conta tiver; paper: localmente), bloqueia ordens e desliga o funil. Reset automático **00:00 BRT** ou manual em `POST /api/v1/risk/reset_breaker {"confirm":"CONFIRMAR"}` (o painel pede confirmação; o reset **não religa** o funil).
- **Segredos:** `NodSecretVault` lê só do ambiente, nunca serializa; `/api/saude` mostra apenas booleanos (`secrets_configured`); logs têm redação global. Telegram opcional (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`). **WhatsApp/QR fora do escopo.**
- Limitações: o risco modela **lotes** (cada ordem de perpétuo é uma posição); em LIVE o stop inicial vai junto na ordem (proteção na corretora) e breakeven/trailing movem o stop do símbolo via `trading-stop` (ambíguo se houver vários lotes no mesmo símbolo); PnL de fechamento LIVE é estimado por preço (não confirmado por `closed-pnl`).
