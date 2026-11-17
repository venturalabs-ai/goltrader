"""Cliente assíncrono IQ Option (binárias/turbo) via WebSocket NÃO OFICIAL.

AVISO: a IQ Option NÃO tem API pública oficial. Este conector usa o protocolo websocket interno da plataforma
(engenharia reversa por terceiros), pode quebrar a qualquer momento e pode violar os Termos de Uso, com risco de
bloqueio/encerramento de conta. Padrão: conta PRACTICE. Mantenha desligado em produção salvo decisão consciente.

Origem: código colado por Wemerson (t313u). Alterações: símbolo desconhecido NÃO cai mais em EURUSD (rejeita);
sem conexão devolve REJECTED (o modo simulado/paper fica na camada de execução); `close()` completo;
tarefa do leitor guardada para cancelamento; futures pendentes falham ao desconectar.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Dict, Optional

import aiohttp
import websockets


class IQOptionAsyncClient:
    """Correlaciona request_id -> asyncio.Future para permitir dezenas de ordens simultâneas no mesmo socket."""

    ACTIVES_MAP = {
        "EURUSD": 1,
        "EURUSD-OTC": 76,
        "GBPUSD": 2,
        "GBPUSD-OTC": 77,
        "USDJPY": 3,
        "BTCUSD": 816,
        "ETHUSD": 817,
        "XAUUSD": 74,
    }

    def __init__(self):
        self.email = os.getenv("IQ_EMAIL", "")
        self.password = os.getenv("IQ_PASSWORD", "")
        self.balance_mode = os.getenv("IQ_BALANCE_MODE", "PRACTICE").upper()
        self.auth_url = "https://auth.iqoption.com/api/v2/login"
        self.ws_url = "wss://iqoption.com/echo/websocket"

        self.ssid: Optional[str] = None
        self.ws = None
        self.user_balance_id: Optional[int] = None
        self.pending_requests: Dict[str, asyncio.Future] = {}
        self.connected = False
        self._reader_task: Optional[asyncio.Task] = None

    @property
    def has_credentials(self) -> bool:
        return bool(self.email and self.password)

    async def authenticate_ssid(self) -> Optional[str]:
        if not self.has_credentials:
            return None
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            payload = {"identifier": self.email, "password": self.password}
            async with session.post(self.auth_url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    self.ssid = data.get("ssid")
                    return self.ssid
        return None

    async def connect(self) -> bool:
        if not self.ssid:
            await self.authenticate_ssid()
        if not self.ssid:
            return False

        self.ws = await websockets.connect(self.ws_url, ping_interval=15)
        self._reader_task = asyncio.create_task(self._reader_loop())

        # Autentica o socket com o SSID
        req_id = str(uuid.uuid4())
        await self.ws.send(json.dumps({"name": "ssid", "msg": self.ssid, "request_id": req_id}))
        await asyncio.sleep(0.5)
        await self._fetch_balances()
        self.connected = self.user_balance_id is not None
        return self.connected

    async def _reader_loop(self):
        try:
            async for raw_msg in self.ws:
                msg = json.loads(raw_msg)
                req_id = msg.get("request_id")
                if req_id and req_id in self.pending_requests:
                    fut = self.pending_requests.pop(req_id)
                    if not fut.done():
                        fut.set_result(msg)
        except Exception:
            pass
        finally:
            self.connected = False
            for fut in self.pending_requests.values():
                if not fut.done():
                    fut.cancel()
            self.pending_requests.clear()

    async def _fetch_balances(self):
        req_id = str(uuid.uuid4())
        fut = asyncio.get_running_loop().create_future()
        self.pending_requests[req_id] = fut
        await self.ws.send(
            json.dumps({
                "name": "sendMessage",
                "msg": {"name": "get-balances", "version": "1.0", "body": {"types_ids": [1, 4]}},
                "request_id": req_id,
            })
        )
        try:
            res = await asyncio.wait_for(fut, timeout=4.0)
            balances = res.get("msg", [])
            target_type = 4 if self.balance_mode == "PRACTICE" else 1
            for b in balances:
                if b.get("type") == target_type:
                    self.user_balance_id = b.get("id")
                    break
        except asyncio.TimeoutError:
            self.pending_requests.pop(req_id, None)

    async def buy_concurrent_binary(
        self,
        symbol: str,
        direction: str,  # "BUY" (call) | "SELL" (put)
        amount: float,
        expiration_min: int = 1,
    ) -> Dict:
        """Dispara ordem Binária/Turbo em paralelo (request_id dedicado); 20+ ordens via asyncio.gather."""
        t0 = time.perf_counter()
        venue = f"IQ_{self.balance_mode}"
        active_id = self.ACTIVES_MAP.get(symbol.upper())
        if active_id is None:
            return {"order_id": None, "symbol": symbol, "venue": venue, "status": "REJECTED",
                    "error": f"ativo '{symbol}' fora do ACTIVES_MAP"}
        if not self.connected or not self.ws:
            return {"order_id": None, "symbol": symbol, "venue": venue, "status": "REJECTED",
                    "error": "IQ Option não conectada"}

        action = "call" if direction.upper() in ("BUY", "CALL") else "put"
        now = int(time.time())
        exp_timestamp = now - (now % 60) + (expiration_min * 60)
        if (exp_timestamp - now) <= 30:
            exp_timestamp += 60

        req_id = f"IQ-{uuid.uuid4().hex[:10]}"
        fut = asyncio.get_running_loop().create_future()
        self.pending_requests[req_id] = fut

        payload = {
            "name": "sendMessage",
            "request_id": req_id,
            "msg": {
                "name": "binary-options.open-option",
                "version": "1.0",
                "body": {
                    "user_balance_id": self.user_balance_id,
                    "active_id": active_id,
                    "option_type_id": 3 if expiration_min <= 5 else 1,
                    "direction": action,
                    "expired": exp_timestamp,
                    "refund_value": 0,
                    "price": float(amount),
                    "value": 0,
                    "profit_percent": 87,
                },
            },
        }

        await self.ws.send(json.dumps(payload))
        try:
            resp = await asyncio.wait_for(fut, timeout=3.5)
            return {
                "order_id": str(resp.get("msg", {}).get("id", req_id)),
                "symbol": symbol,
                "venue": venue,
                "side": direction.upper(),
                "amount": amount,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
                "status": "FILLED",
            }
        except asyncio.TimeoutError:
            self.pending_requests.pop(req_id, None)
            return {"order_id": req_id, "symbol": symbol, "venue": venue, "status": "TIMEOUT"}
        except asyncio.CancelledError:
            raise

    async def close(self):
        """Encerra leitor e socket (idempotente)."""
        self.connected = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        for fut in self.pending_requests.values():
            if not fut.done():
                fut.cancel()
        self.pending_requests.clear()
