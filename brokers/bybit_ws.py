"""WebSocket privado Bybit V5 (order / execution / position / wallet) com reconexão automática.

- Autentica com HMAC (`GET/realtime{expires}`), assina os tópicos e mantém ping/pong.
- Qualquer queda (erro, fechamento, falta de pong) → backoff exponencial → reconecta, reautentica,
  reassina e chama `on_connect(reconectado=True)` para o chamador reconciliar o estado via REST.
- `connect` e `sleep` injetáveis (testes sem rede).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import random
import time
from typing import Any, Awaitable, Callable, Optional, Sequence

from core.retry import backoff_delays

log = logging.getLogger("goltrader.bybit.ws")

DEFAULT_TOPICS = ("order", "execution", "position", "wallet")


def ws_signature(secret: str, expires: int) -> str:
    return hmac.new(secret.encode(), f"GET/realtime{expires}".encode(), hashlib.sha256).hexdigest()


class WSAuthError(RuntimeError):
    pass


class BybitPrivateWS:
    def __init__(self, url: str, api_key: str, api_secret: str, on_message: Callable[[str, list], Awaitable[None]],
                 topics: Sequence[str] = DEFAULT_TOPICS, on_connect: Optional[Callable[[bool], Awaitable[None]]] = None,
                 connect: Optional[Callable[[str], Awaitable[Any]]] = None, ping_interval: float = 20.0,
                 time_fn: Callable[[], float] = time.time, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 rng: Callable[[], float] = random.random, max_backoff: float = 30.0):
        self.url, self.api_key, self._secret = url, api_key, api_secret
        self.on_message, self.on_connect = on_message, on_connect
        self.topics = tuple(topics)
        self._connect = connect or self._default_connect
        self.ping_interval = ping_interval
        self._time, self._sleep, self._rng, self.max_backoff = time_fn, sleep, rng, max_backoff
        self._stop = False
        self.connected = False
        self.reconnects = 0
        self.last_message_ts = 0.0
        self._ws: Any = None
        self._was_up = False

    @staticmethod
    async def _default_connect(url: str):
        import websockets
        return await websockets.connect(url, ping_interval=None, close_timeout=5)

    def stop(self) -> None:
        self._stop = True

    async def close(self) -> None:
        self._stop = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def run_forever(self) -> None:
        failures = 0
        first = True
        while not self._stop:
            try:
                await self._session(reconnect=not first)
                first = False
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                first = False
                self.connected = False
                if self._was_up:
                    failures, self._was_up = 0, False
                if self._stop:
                    break
                delay = list(backoff_delays(failures + 1, base=1.0, max_delay=self.max_backoff, rng=self._rng))[-1]
                failures += 1
                self.reconnects += 1
                log.warning("WS privado caiu (%s: %s); reconectando em %.1fs", type(e).__name__, e, delay)
                await self._sleep(delay)
        self.connected = False

    async def _session(self, reconnect: bool) -> None:
        ws = await self._connect(self.url)
        self._ws = ws
        try:
            expires = int((self._time() + 10) * 1000)
            await ws.send(json.dumps({"op": "auth", "args": [self.api_key, expires, ws_signature(self._secret, expires)]}))
            await self._await_op(ws, "auth")
            await ws.send(json.dumps({"op": "subscribe", "args": list(self.topics)}))
            await self._await_op(ws, "subscribe")
            self.connected = True
            self._was_up = True
            self.last_message_ts = self._time()
            log.info("WS privado conectado (%s)", ",".join(self.topics))
            if self.on_connect:
                await self.on_connect(reconnect)
            awaiting_pong_since: Optional[float] = None
            while not self._stop:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self.ping_interval)
                except asyncio.TimeoutError:
                    if awaiting_pong_since is not None:
                        raise ConnectionError("sem pong do servidor")
                    await ws.send(json.dumps({"op": "ping"}))
                    awaiting_pong_since = self._time()
                    continue
                self.last_message_ts = self._time()
                awaiting_pong_since = None
                msg = json.loads(raw)
                if msg.get("op") in ("pong", "ping") or msg.get("ret_msg") == "pong":
                    continue
                if msg.get("topic"):
                    data = msg.get("data") or []
                    await self.on_message(msg["topic"].split(".")[0], data if isinstance(data, list) else [data])
                elif msg.get("success") is False:
                    raise WSAuthError(f"op {msg.get('op')} falhou: {msg.get('ret_msg')}")
        finally:
            self.connected = False
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def _await_op(self, ws, op: str) -> None:
        for _ in range(20):
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            if msg.get("op") == op:
                if msg.get("success") is not True:
                    raise WSAuthError(f"{op} recusado: {msg.get('ret_msg')}")
                return
        raise WSAuthError(f"sem resposta de {op}")
