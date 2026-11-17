"""Estado do motor, guardas de risco e execução de ordens (paper por padrão; live só com todas as travas)."""
from __future__ import annotations

import asyncio
import secrets
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import logging

from config import env_bool, env_float, env_int
from connectors.bybit_async import BybitV5AsyncClient
from connectors.iqoption_async import IQOptionAsyncClient
from models import SmartOrderRequest
from nod_core.agent_runtime import NodSecretVault, NodTrustGate, RedactingFilter, TelegramNotifier
from risk.brokers import BybitBroker
from risk.risk_manager import InstitutionalRiskManager

BRT = timezone(timedelta(hours=-3))
BYBIT_CATEGORY = {"BYBIT_PERP": "linear", "BYBIT_SPOT": "spot", "BYBIT_OPT": "option"}


class EngineState:
    def __init__(self):
        self.analyzer_mode: bool = env_bool("ANALYZER_MODE", True)  # True = paper/analyzer
        self.autonomous_funnel: bool = False  # sempre desligado ao iniciar
        self.kill_switch: bool = False
        self.data_source: str = "SIMULADO"  # preenchido pelo engine ('SIMULADO' | 'BYBIT')
        self.max_concurrent_ops: int = 25
        self.active_positions: List[Dict] = []
        self.execution_logs: List[Dict] = []
        self.order_times: deque = deque()
        self.daily_pnl_usd: float = 0.0
        self._pnl_day: str = self._today()
        self._sems: Dict[int, asyncio.Semaphore] = {}
        self.bybit: Optional[BybitV5AsyncClient] = None
        self.iq: Optional[IQOptionAsyncClient] = None
        self.prices: Dict[str, float] = {}  # último preço por símbolo (atualizado pelo engine a cada tick)
        self.vault = NodSecretVault()
        self.notifier = TelegramNotifier(self.vault)
        self.risk = InstitutionalRiskManager(
            broker=BybitBroker(self.get_bybit), notifier=self.notifier,
            on_realized=lambda pnl, pos: self.register_pnl(pnl),
            on_breaker=self._on_breaker,
        )
        self.gate = NodTrustGate(self)

    def _on_breaker(self, tripped: bool):
        if tripped:
            self.autonomous_funnel = False  # breaker desliga o funil (reset NÃO religa)

    @staticmethod
    def _today() -> str:
        return datetime.now(BRT).strftime("%Y-%m-%d")

    def semaphore(self) -> asyncio.Semaphore:
        loop_id = id(asyncio.get_running_loop())
        if loop_id not in self._sems:
            self._sems[loop_id] = asyncio.Semaphore(self.max_concurrent_ops)
        return self._sems[loop_id]

    # ---- limites
    def roll_day(self):
        if self._today() != self._pnl_day:
            self._pnl_day, self.daily_pnl_usd = self._today(), 0.0

    def register_pnl(self, delta_usd: float):
        """Ponto de integração do PnL REALIZADO (TODO: ligar a /v5/position/closed-pnl). Estoura limite => kill switch."""
        self.roll_day()
        self.daily_pnl_usd += delta_usd
        limit = env_float("GOLTRADER_MAX_DAILY_LOSS_USD", 200)
        if limit > 0 and self.daily_pnl_usd <= -limit:
            self.trip_kill_switch()

    def trip_kill_switch(self):
        self.kill_switch = True
        self.autonomous_funnel = False
        self.analyzer_mode = True  # volta para paper

    def mode_label(self, paper: bool) -> str:
        if paper:
            return "SANDBOX_ANALYZER"
        return "LIVE_TESTNET" if self.bybit_testnet() else "LIVE_PRODUCTION"

    @staticmethod
    def bybit_testnet() -> bool:
        import os
        return os.getenv("BYBIT_TESTNET", "true").lower() == "true"

    def get_bybit(self) -> BybitV5AsyncClient:
        if self.bybit is None:
            self.bybit = BybitV5AsyncClient()
        return self.bybit

    def get_iq(self) -> IQOptionAsyncClient:
        if self.iq is None:
            self.iq = IQOptionAsyncClient()
        return self.iq

    async def close(self):
        for c in (self.bybit, self.iq):
            if c is not None:
                await c.close()


state = EngineState()


def new_order_id() -> str:
    return f"GT-{int(time.time() * 1000)}-{secrets.token_hex(4)}"


def _record(order: SmartOrderRequest, status: str, paper: bool, origin: str, started: float, **extra) -> Dict:
    rec = {
        "order_id": extra.pop("order_id", None) or new_order_id(),
        "symbol": order.symbol, "venue": order.venue, "side": order.side, "amount": order.amount,
        "mode": state.mode_label(paper), "paper": paper, "origin": origin, "data_source": state.data_source,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2), "status": status,
        "timestamp": time.strftime("%H:%M:%S"), "time": time.strftime("%H:%M:%S"),
    }
    rec.update(extra)
    state.active_positions.insert(0, rec)
    state.active_positions = state.active_positions[:50]
    state.execution_logs.insert(0, rec)
    state.execution_logs = state.execution_logs[:500]
    return rec


async def _live_bybit(order: SmartOrderRequest) -> Dict:
    client = state.get_bybit()
    if not client.has_credentials:
        return {"status": "REJECTED", "error": "credenciais Bybit ausentes"}
    cat = BYBIT_CATEGORY[order.venue]
    max_lev = env_int("GOLTRADER_MAX_LEVERAGE", 10)
    if cat == "linear":
        lev = order.leverage or 1
        if lev > max_lev:
            return {"status": "REJECTED", "error": f"alavancagem {lev}x acima do máximo {max_lev}x"}
        r = await client.set_leverage(order.symbol, lev)
        if r.get("retCode") not in (0, 110043):  # 110043 = leverage not modified
            return {"status": "REJECTED", "error": f"set_leverage: {r.get('retMsg')}"}
    if cat == "option" and not order.price:
        return {"status": "REJECTED", "error": "ordem de opção exige preço limite"}
    resp = await client.place_order(
        category=cat, symbol=order.symbol, side=order.side.capitalize(), qty=f"{order.amount:.10g}",
        order_type="Limit" if order.price else "Market",
        price=f"{order.price:.10g}" if order.price else None,
        take_profit=f"{order.take_profit:.10g}" if order.take_profit and cat == "linear" else None,
        stop_loss=f"{order.stop_loss:.10g}" if order.stop_loss and cat == "linear" else None,
    )
    if resp.get("retCode") == 0:
        return {"status": "SUBMITTED", "order_id": resp.get("result", {}).get("orderId"), "exchange_latency_ms": resp.get("latency_ms")}
    return {"status": "REJECTED", "error": f"Bybit retCode={resp.get('retCode')} {resp.get('retMsg')}"}


async def _live_iq(order: SmartOrderRequest) -> Dict:
    if order.venue == "IQ_DIGITAL":
        return {"status": "REJECTED", "error": "IQ_DIGITAL ainda não implementado (apenas IQ_BINARY)"}
    iq = state.get_iq()
    if not iq.connected:
        try:
            await asyncio.wait_for(iq.connect(), timeout=15)
        except Exception as e:
            return {"status": "REJECTED", "error": f"IQ Option indisponível: {type(e).__name__}"}
    sym = order.symbol.upper().replace("USDT", "USD")
    res = await iq.buy_concurrent_binary(sym, order.side, order.amount, order.expiration_min or 1)
    return {"status": res["status"], "order_id": res.get("order_id"), **({"error": res["error"]} if "error" in res else {})}


def _register_perp(order: SmartOrderRequest, paper: bool, stop_loss: Optional[float]):
    """Registra o lote no gerenciador de risco (breakeven/trailing/breaker). Só perpétuos lineares."""
    if order.venue != "BYBIT_PERP":
        return None
    entry = state.prices.get(order.symbol, 0.0)
    if entry <= 0:
        return None
    return state.risk.register_position(
        symbol=order.symbol, side=order.side, entry=entry, qty=order.amount, leverage=order.leverage or 1,
        stop_loss=stop_loss, paper=paper, data_source=state.data_source, take_profit=order.take_profit,
    )


async def execute_single_order(order: SmartOrderRequest, origin: str = "MANUAL") -> Dict:
    """Todo caminho de ordem passa por state.gate.verify_order (NodTrustGate = ponto único de aprovação).
    Paper no analyzer; live só com analyzer_mode=False (toggle_mode+CONFIRMAR+GOLTRADER_ALLOW_LIVE).
    Ordens do FUNIL com dados SIMULADOS são sempre paper."""
    async with state.semaphore():
        t0 = time.perf_counter()
        decision = state.gate.verify_order(order, origin)
        if not decision.allowed:
            return _record(order, "REJECTED", True, origin, t0, error=decision.message, trust_code=decision.code)
        paper = decision.paper

        # stop inicial: o informado ou o padrão do risk manager (em LIVE vai junto na ordem = proteção na corretora)
        stop = order.stop_loss
        if order.venue == "BYBIT_PERP" and not stop:
            stop = state.risk.default_stop(order.side, state.prices[order.symbol])
            order = order.model_copy(update={"stop_loss": stop})

        if paper:
            await asyncio.sleep(0.015)  # latência simulada
            rec = _record(order, "FILLED_PAPER", True, origin, t0)
            pos = _register_perp(order, True, stop)
            if pos:
                rec["position_id"] = pos.id
            return rec

        try:
            res = await (_live_bybit(order) if order.venue.startswith("BYBIT") else _live_iq(order))
        except Exception as e:
            res = {"status": "ERROR", "error": f"{type(e).__name__}: {state.vault.redact(str(e))[:160]}"}
        status = res.pop("status")
        rec = _record(order, status, False, origin, t0, **res)
        if status == "SUBMITTED":
            pos = _register_perp(order, False, stop)
            if pos:
                rec["position_id"] = pos.id
        return rec
