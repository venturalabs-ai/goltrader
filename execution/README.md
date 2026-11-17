# execution/ — Bybit V5 24/7 (Testnet por padrão)

Componentes: `brokers/bybit*.py` (REST assinado, WS privado, adapter spot + perp linear USDT; opções só dados públicos),
`execution/engine.py` (ordens idempotentes, risco, reconciliação, kill switch), `execution/state.py` (SQLite),
`execution/worker.py` (processo 24/7), `execution/cli.py`, `execution/healthcheck.py`, `core/safety.py`, `core/lease.py`.

## Modo (core/safety.py)
Testnet é o padrão. **LIVE só se**: `BYBIT_TESTNET=false` **e** `GOLTRADER_ALLOW_LIVE=true` **e** `ANALYZER_MODE` ≠ `true`
**e** confirmação `CONFIRMAR` (`GOLTRADER_LIVE_CONFIRMATION=CONFIRMAR`, só depois de o dono pedir). Falta de qualquer uma → `LiveBlocked`/`LiveNotConfirmed` (o worker não sobe).

## Variáveis
| Variável | Padrão | Uso |
|---|---|---|
| `BYBIT_TESTNET` | `true` | `false` pede conta real |
| `GOLTRADER_ALLOW_LIVE` | `false` | trava extra para LIVE |
| `ANALYZER_MODE` | (vazio) | `true` bloqueia LIVE |
| `BYBIT_TESTNET_API_KEY/SECRET` | — | chaves Testnet (ou legado `BYBIT_API_KEY/SECRET`) |
| `BYBIT_REAL_API_KEY/SECRET` | — | chaves reais (só LIVE) |
| `GOLTRADER_STATE_DIR` | `state` | SQLite, heartbeat, lease, KILL_SWITCH |
| `GOLTRADER_MAX_POSITION_USD` | 1000 | exposição líquida máx. por símbolo |
| `GOLTRADER_MAX_ORDER_USD` | 500 | notional máx. por ordem |
| `GOLTRADER_MAX_DAILY_LOSS_USD` | 100 | perda diária (equity início do dia UTC − atual) que trava e aciona kill |
| `GOLTRADER_MAX_ORDERS_PER_MIN` | 20 | ordens por minuto |
| `GOLTRADER_KILL_ON_DAILY_LOSS` | `true` | fecha tudo reduce-only ao estourar a perda diária |
| `GOLTRADER_LEASE_TTL` | 30 | segundos do lease de instância única |

## Operação
```
python -m execution.worker                       # processo 24/7 (ocioso se não houver chaves)
python -m execution.healthcheck [max_idade_s]    # exit 0 = saudável (heartbeat + lease)
python -m execution.cli status | kill "motivo" | clear-kill --confirm | reconcile | submit linear BTCUSDT buy 0.001
```
- **Kill switch**: arquivo `<state>/KILL_SWITCH` (também `logs/KILL_SWITCH`). Bloqueia toda ordem nova; o worker cancela ordens e fecha posições lineares com market `reduceOnly`. Spot: só cancela ordens (não vende saldo).
- **Idempotência**: toda ordem tem `orderLinkId` (gerado `gt-<ms>-<hex>` ou o `client_id`); é gravada como PENDING antes do envio; retry/timeout reaproveita o mesmo id (retCode 110072 → devolve a ordem existente).
- **Reconciliação**: na partida, a cada reconexão do WS e a cada 5 min (ordens abertas/ausentes → `LOST`, posições, execuções, equity).
- **Limites do WS/REST**: backoff exponencial com jitter; token bucket local + respeito aos headers `X-Bapi-Limit-*`.

## Testes
`pip install -r requirements.txt pytest && pytest tests` — sem rede (o `conftest.py` bloqueia sockets).
Adapters extras (`brokers/requirements-brokers.txt`): ccxt (Binance/OKX/Coinbase/Kraken), ib_insync (IBKR), cTrader (transporte pendente), IQ Option (não oficial).
