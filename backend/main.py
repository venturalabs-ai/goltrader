"""Goltrader Pulse — API FastAPI + WebSocket (/ws/pulse) + funil de confluência + execução multi-corretora.

Segurança: CORS por env, token Bearer em todo POST /api/v1/* e no WS (?token=), Testnet/paper por padrão,
LIVE só com GOLTRADER_ALLOW_LIVE=true + POST toggle_mode {"confirm":"CONFIRMAR"}, kill switch, limites de risco.
Rodar:  uvicorn main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")  # .env local, ignorado pelo Git

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from config import api_token, cors_origins, env_bool, env_float, env_int  # noqa: E402
from engine import TICK_S, Engine  # noqa: E402
from execution import execute_single_order, state  # noqa: E402
from nod_core.agent_runtime import install_log_redaction  # noqa: E402
from funnel_engine import classify_buildup, detect_order_blocks, evaluate_inefficiency_funnel  # noqa: E402,F401
from models import KillSwitchBody, ResetBreakerBody, SmartOrderRequest, ToggleModeBody  # noqa: E402

VERSION = "3.2.0"
install_log_redaction(state.vault)
engine: Optional[Engine] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = Engine()
    await engine.start()
    try:
        yield
    finally:
        await engine.stop()
        await state.close()
        engine = None


app = FastAPI(title="GolTrader Pulse Engine", version=VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


# ------------------------------------------------------------------ auth
async def require_auth(request: Request):
    token = api_token()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="GOLTRADER_API_TOKEN não definido: o servidor só opera em modo local/leitura e recusa chamadas que alteram estado.",
        )
    scheme, _, cred = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(cred.encode(), token.encode()):
        raise HTTPException(status_code=401, detail="token inválido", headers={"WWW-Authenticate": "Bearer"})


def _ws_allowed(ws: WebSocket) -> bool:
    token = api_token()
    if token:
        return secrets.compare_digest((ws.query_params.get("token") or "").encode(), token.encode())
    host = ws.client.host if ws.client else ""
    return host in ("127.0.0.1", "::1", "localhost")  # sem token: somente loopback (leitura)


# ------------------------------------------------------------------ rotas
@app.get("/api/saude")
async def saude():
    return {
        "status": "ok",
        "version": VERSION,
        "data_source": state.data_source,
        "simulated": state.data_source == "SIMULADO",
        "analyzer_mode": state.analyzer_mode,
        "autonomous_funnel": state.autonomous_funnel,
        "kill_switch": state.kill_switch,
        "token_configured": bool(api_token()),
        "live_allowed": env_bool("GOLTRADER_ALLOW_LIVE", False),
        "bybit_testnet": state.bybit_testnet(),
        "secrets_configured": state.vault.masked_status(),  # só booleanos, nunca valores
        "equity_source": state.risk.equity_source,
        "breaker_tripped": state.risk.breaker_tripped,
        "iq_enabled": env_bool("GOLTRADER_ENABLE_IQ", False),
        "engine_running": engine is not None and bool(engine.tasks),
    }


@app.post("/api/v1/place_concurrent_basket", dependencies=[Depends(require_auth)])
async def place_concurrent_basket(orders: List[SmartOrderRequest]):
    """Dispara a cesta em paralelo (asyncio.gather). Paper no analyzer; live só com todas as travas."""
    if not orders or len(orders) > 20:
        raise HTTPException(status_code=422, detail="cesta deve ter de 1 a 20 ordens")
    results = await asyncio.gather(*(execute_single_order(o, origin="MANUAL") for o in orders))
    return {"status": "ok", "executed_count": len(results), "executions": results}


@app.post("/api/v1/toggle_mode", dependencies=[Depends(require_auth)])
async def toggle_mode(body: Optional[ToggleModeBody] = None):
    if state.analyzer_mode:  # indo para LIVE
        if state.kill_switch:
            raise HTTPException(status_code=409, detail="kill switch ativo: reinicie-o antes de ir para LIVE")
        if not env_bool("GOLTRADER_ALLOW_LIVE", False):
            raise HTTPException(status_code=403, detail="LIVE bloqueado: defina GOLTRADER_ALLOW_LIVE=true no servidor")
        if body is None or body.confirm != "CONFIRMAR":
            raise HTTPException(status_code=403, detail='LIVE exige body {"confirm":"CONFIRMAR"}')
        state.analyzer_mode = False
    else:
        state.analyzer_mode = True
    return {"analyzer_mode": state.analyzer_mode, "mode": state.mode_label(state.analyzer_mode),
            "bybit_testnet": state.bybit_testnet()}


@app.post("/api/v1/toggle_autonomous", dependencies=[Depends(require_auth)])
async def toggle_autonomous():
    if not state.autonomous_funnel and state.kill_switch:
        raise HTTPException(status_code=409, detail="kill switch ativo")
    if not state.autonomous_funnel and state.risk.breaker_tripped:
        raise HTTPException(status_code=409, detail="circuit breaker de drawdown ativo")
    state.autonomous_funnel = not state.autonomous_funnel
    return {"autonomous_funnel": state.autonomous_funnel, "data_source": state.data_source,
            "paper_only": state.analyzer_mode or state.data_source != "BYBIT"}


@app.post("/api/v1/kill_switch", dependencies=[Depends(require_auth)])
async def kill_switch(body: Optional[KillSwitchBody] = None):
    active = True if body is None else body.active
    cancel = None
    if active:
        was_live = not state.analyzer_mode
        state.trip_kill_switch()
        client = state.bybit
        if was_live and client is not None and client.has_credentials:
            try:  # melhor esforço: cancela ordens abertas em perpétuos USDT (não fecha posições)
                cancel = (await client.cancel_all("linear", "USDT")).get("retMsg")
            except Exception as e:
                cancel = f"falha ao cancelar: {type(e).__name__}"
    else:
        state.kill_switch = False  # reativar NÃO religa funil nem LIVE
    return {"kill_switch": state.kill_switch, "analyzer_mode": state.analyzer_mode,
            "autonomous_funnel": state.autonomous_funnel, "cancel_all": cancel}


@app.post("/api/v1/risk/reset_breaker", dependencies=[Depends(require_auth)])
async def reset_breaker(body: Optional[ResetBreakerBody] = None):
    """Reset MANUAL do circuit breaker de drawdown (exige {"confirm":"CONFIRMAR"}). Não religa o funil nem o LIVE."""
    try:
        res = state.risk.reset_breaker(body.confirm if body else "")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"breaker_tripped": state.risk.breaker_tripped, **res, "autonomous_funnel": state.autonomous_funnel}


@app.websocket("/ws/pulse")
async def pulse_stream(ws: WebSocket):
    if not _ws_allowed(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        while True:
            if engine is not None and engine.latest is not None:
                payload = engine.latest
            else:  # sem loop de fundo (ex.: testes): gera um tick sob demanda
                tmp = engine or Engine()
                payload = await tmp.tick()
            # estado de controle sempre fresco (inclui mudanças feitas entre ticks)
            payload = {**payload, "analyzer_mode": state.analyzer_mode, "autonomous_funnel": state.autonomous_funnel,
                       "kill_switch": state.kill_switch, "active_positions": state.active_positions[:8],
                       "risk_telemetry": state.risk.telemetry(), "trust_ledger": state.gate.recent(20)}
            await ws.send_text(json.dumps(payload, default=str))
            await asyncio.sleep(TICK_S)
    except WebSocketDisconnect:
        pass
