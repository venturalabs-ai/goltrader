"""Worker 24/7 da execução Bybit.

Faz: lease (1 instância), reconciliação na partida e após cada reconexão do WS, WS privado
(order/execution/position/wallet), fila de comandos (kill/submit/reconcile), vigia do arquivo KILL_SWITCH,
heartbeat em <state>/heartbeat.json (usado pelo healthcheck e pelo watchdog).
Sem credenciais (ex.: chaves da Testnet ainda não colocadas) ele sobe em modo ocioso e informa no heartbeat.

Execução:  python -m execution.worker
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any, Dict, Optional

from brokers.base import Mode, OrderRequest
from brokers.bybit import BybitAdapter, credentials, make_rest
from brokers.bybit_client import LIVE_WS_PRIVATE, TESTNET_WS_PRIVATE
from brokers.bybit_ws import BybitPrivateWS
from core.lease import Lease
from core.safety import resolve_mode, state_dir
from .engine import ExecutionEngine
from .risk import RiskGuard, RiskLimits
from .state import StateStore

log = logging.getLogger("goltrader.worker")


def build_engine(mode: Mode, sdir: Path, session=None, limits: Optional[RiskLimits] = None) -> ExecutionEngine:
    rest = make_rest(mode, session=session)
    adapters = {c: BybitAdapter(mode, confirmation=os.getenv("GOLTRADER_LIVE_CONFIRMATION"), category=c, rest=rest)
                for c in ("spot", "linear")}
    store = StateStore(sdir / "goltrader.db")
    return ExecutionEngine(adapters, store, RiskGuard(limits or RiskLimits.from_env()), mode, sdir)


class Worker:
    def __init__(self, engine: ExecutionEngine, lease: Lease, sdir: Path, ws: Optional[BybitPrivateWS] = None,
                 heartbeat_s: float = 5.0, reconcile_s: float = 300.0, command_poll_s: float = 1.0):
        self.engine, self.lease, self.sdir, self.ws = engine, lease, Path(sdir), ws
        self.heartbeat_s, self.reconcile_s, self.command_poll_s = heartbeat_s, reconcile_s, command_poll_s
        self.stop_event = asyncio.Event()
        self.lease_ok = False
        self.started = time.time()
        self.last_error: Optional[str] = None

    # ---- callbacks do WS ----
    async def on_ws_message(self, topic: str, items: list) -> None:
        await asyncio.to_thread(self.engine.handle_ws, topic, items)

    async def on_ws_connect(self, reconnect: bool) -> None:
        """Depois de (re)conectar, reconcilia: o que aconteceu enquanto o WS estava fora."""
        await self._reconcile("ws_reconectado" if reconnect else "ws_conectado")

    async def _reconcile(self, why: str) -> None:
        try:
            r = await asyncio.to_thread(self.engine.reconcile)
            log.info("reconcile (%s): %s", why, r)
        except Exception as e:  # noqa: BLE001
            self.last_error = f"reconcile: {e}"
            log.exception("reconcile falhou")

    # ---- comandos ----
    async def process_commands(self) -> None:
        store = self.engine.store
        while True:
            cmd = store.next_command()
            if not cmd:
                return
            try:
                payload: Dict[str, Any] = json.loads(cmd["payload"] or "{}")
                if cmd["kind"] == "kill":
                    res: Any = await asyncio.to_thread(self.engine.kill_switch, payload.get("reason", "comando"))
                elif cmd["kind"] == "reconcile":
                    res = await asyncio.to_thread(self.engine.reconcile)
                elif cmd["kind"] == "submit":
                    req = OrderRequest(payload["symbol"], payload["side"], float(payload["qty"]), payload.get("order_type", "market"),
                                       payload.get("price"), bool(payload.get("reduce_only", False)), payload.get("client_id"))
                    r = await asyncio.to_thread(self.engine.submit, req, payload.get("category", "linear"))
                    res = {"ok": r.ok, "order_id": r.order_id, "status": r.status, "link": req.client_id}
                else:
                    raise ValueError(f"comando desconhecido: {cmd['kind']}")
                store.finish_command(cmd["id"], "done", res)
            except Exception as e:  # noqa: BLE001
                store.finish_command(cmd["id"], "error", {"error": f"{type(e).__name__}: {e}"})

    # ---- heartbeat ----
    def write_heartbeat(self) -> None:
        st = self.engine.status()
        st.update({"ts": time.time(), "started": self.started, "pid": os.getpid(), "lease_ok": self.lease_ok,
                   "ws_connected": bool(self.ws and self.ws.connected), "ws_reconnects": self.ws.reconnects if self.ws else 0,
                   "idle_sem_credenciais": not self.engine.has_credentials, "last_error": self.last_error})
        tmp = self.sdir / "heartbeat.json.tmp"
        tmp.write_text(json.dumps(st))
        os.replace(tmp, self.sdir / "heartbeat.json")

    async def tick(self) -> None:
        """Uma volta do laço de manutenção (separada para testes)."""
        self.lease_ok = self.lease.renew()
        if not self.lease_ok:
            self.last_error = "lease perdido: outra instância opera; parando"
            log.error(self.last_error)
            self.stop_event.set()
        elif self.engine.kill_pending():
            log.error("KILL_SWITCH detectado: fechando posições reduce-only")
            await asyncio.to_thread(self.engine.kill_switch, "flag externo", False)
        await self.process_commands()
        self.write_heartbeat()

    async def run(self) -> None:
        self.lease_ok = self.lease.acquire()
        if not self.lease_ok:
            self.last_error = f"lease em uso por {self.lease.holder()}"
            self.write_heartbeat()
            raise SystemExit(f"Outra instância já opera ({self.lease.holder()}). Saindo.")
        tasks = []
        try:
            if self.engine.has_credentials:
                await self._reconcile("partida")
                if self.ws:
                    tasks.append(asyncio.create_task(self.ws.run_forever()))
            else:
                log.warning("Sem credenciais Bybit: modo ocioso (heartbeat ativo, sem ordens).")
            next_reconcile = time.time() + self.reconcile_s
            last_hb = 0.0
            while not self.stop_event.is_set():
                if time.time() - last_hb >= self.heartbeat_s:
                    await self.tick()
                    last_hb = time.time()
                else:
                    await self.process_commands()
                if self.engine.has_credentials and time.time() >= next_reconcile:
                    await self._reconcile("periodico")
                    next_reconcile = time.time() + self.reconcile_s
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=self.command_poll_s)
                except asyncio.TimeoutError:
                    pass
        finally:
            if self.ws:
                await self.ws.close()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.lease.release()
            self.lease_ok = False
            self.write_heartbeat()
            log.info("worker encerrado")


def main() -> None:
    logging.basicConfig(level=os.getenv("GOLTRADER_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    mode = resolve_mode()
    sdir = state_dir()
    log.info("Goltrader worker | modo=%s | estado=%s", mode.value.upper(), sdir)
    engine = build_engine(mode, sdir)
    key, sec = credentials(mode)
    ws = None
    if key and sec:
        ws = BybitPrivateWS(LIVE_WS_PRIVATE if mode is Mode.LIVE else TESTNET_WS_PRIVATE, key, sec, on_message=None)  # type: ignore[arg-type]
    lease = Lease(sdir / "lease.db", ttl=float(os.getenv("GOLTRADER_LEASE_TTL", "30")))
    worker = Worker(engine, lease, sdir, ws)
    if ws:
        ws.on_message, ws.on_connect = worker.on_ws_message, worker.on_ws_connect

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        for s in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(s, worker.stop_event.set)
            except NotImplementedError:   # Windows
                pass
        await worker.run()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
