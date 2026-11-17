"""Cliente REST Bybit V5: assinatura HMAC-SHA256, rate limit, retry/backoff, ressincronização de relógio.

Sem rede em testes: `session` é injetável (objeto com `.request(method, url, headers=, data=, timeout=)`).
Regras de retry:
  - GET: repete em erro de rede, HTTP 429/5xx e retCodes transitórios.
  - POST: só repete se `idempotent=True` (ordens com orderLinkId, cancelamentos).
Credenciais nunca são logadas.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlencode

from core.ratelimit import TokenBucket
from core.retry import backoff_delays

log = logging.getLogger("goltrader.bybit")

TESTNET_REST = "https://api-testnet.bybit.com"
LIVE_REST = "https://api.bybit.com"
TESTNET_WS_PRIVATE = "wss://stream-testnet.bybit.com/v5/private"
LIVE_WS_PRIVATE = "wss://stream.bybit.com/v5/private"

RET_RATE_LIMIT = 10006
RET_TIMESTAMP = 10002
RET_SERVER_BUSY = {10016, 10019}
RET_DUPLICATE_LINK = 110072   # orderLinkId duplicado
TRANSIENT_RET = {RET_RATE_LIMIT, RET_TIMESTAMP, *RET_SERVER_BUSY}


class BybitAPIError(RuntimeError):
    def __init__(self, ret_code: int, ret_msg: str, payload: Optional[dict] = None):
        super().__init__(f"Bybit retCode={ret_code}: {ret_msg}")
        self.ret_code, self.ret_msg, self.payload = ret_code, ret_msg, payload or {}


class BybitTransportError(RuntimeError):
    """Falha de rede/HTTP depois de esgotar as tentativas."""


def sign(secret: str, timestamp: str, api_key: str, recv_window: str, payload: str) -> str:
    return hmac.new(secret.encode(), f"{timestamp}{api_key}{recv_window}{payload}".encode(), hashlib.sha256).hexdigest()


def compact_json(body: dict) -> str:
    return json.dumps(body, separators=(",", ":"))


class BybitRest:
    def __init__(self, base_url: str, api_key: str = "", api_secret: str = "", session=None,
                 recv_window: int = 5000, limiter: Optional[TokenBucket] = None, max_retries: int = 4,
                 timeout: float = 10.0, sleep: Callable[[float], None] = time.sleep,
                 time_fn: Callable[[], float] = time.time, rng: Callable[[], float] = random.random):
        self.base_url = base_url.rstrip("/")
        self.api_key, self._secret = api_key, api_secret
        self.recv_window = str(recv_window)
        self.limiter = limiter or TokenBucket(rate_per_sec=10, burst=10)
        self.max_retries, self.timeout = max_retries, timeout
        self._sleep, self._time, self._rng = sleep, time_fn, rng
        self.time_offset_ms = 0
        if session is None:
            import requests
            session = requests.Session()
        self.session = session

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self._secret)

    def _now_ms(self) -> int:
        return int(self._time() * 1000) + self.time_offset_ms

    def sync_time(self) -> int:
        """Mede o desvio do relógio local vs servidor (/v5/market/time)."""
        j = self.request("GET", "/v5/market/time", auth=False, _resync=False)
        server_ms = int(j.get("time") or int(j["result"]["timeSecond"]) * 1000)
        self.time_offset_ms = server_ms - int(self._time() * 1000)
        return self.time_offset_ms

    def request(self, method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None,
                auth: bool = True, idempotent: Optional[bool] = None, _resync: bool = True) -> Dict[str, Any]:
        method = method.upper()
        if idempotent is None:
            idempotent = method == "GET"
        if auth and not self.has_credentials:
            raise RuntimeError("Credenciais Bybit ausentes no ambiente (.env).")
        delays = list(backoff_delays(self.max_retries, base=0.5, max_delay=20.0, rng=self._rng))
        attempt = 0
        while True:
            try:
                return self._once(method, path, params, body, auth)
            except BybitAPIError as e:
                retry = idempotent and e.ret_code in TRANSIENT_RET and attempt < self.max_retries
                if not retry:
                    raise
                if e.ret_code == RET_TIMESTAMP and _resync:
                    try:
                        self.sync_time()
                    except Exception:  # noqa: BLE001
                        pass
                err: BaseException = e
            except BybitTransportError as e:
                if not (idempotent and attempt < self.max_retries):
                    raise
                err = e
            d = delays[attempt]
            log.warning("bybit %s %s falhou (%s); tentativa %d/%d em %.2fs", method, path, err, attempt + 1, self.max_retries, d)
            if isinstance(err, BybitAPIError) and err.ret_code == RET_RATE_LIMIT:
                self.limiter.penalize(d)
            self._sleep(d)
            attempt += 1

    def _once(self, method: str, path: str, params: Optional[dict], body: Optional[dict], auth: bool) -> Dict[str, Any]:
        self.limiter.acquire()
        headers = {"Content-Type": "application/json"}
        data = None
        url = f"{self.base_url}{path}"
        if method == "GET":
            qs = urlencode(params or {})
            if qs:
                url = f"{url}?{qs}"
            payload = qs
        else:
            data = compact_json(body or {})
            payload = data
        if auth:
            ts = str(self._now_ms())
            headers.update({"X-BAPI-API-KEY": self.api_key, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": self.recv_window,
                            "X-BAPI-SIGN": sign(self._secret, ts, self.api_key, self.recv_window, payload)})
        try:
            r = self.session.request(method, url, headers=headers, data=data, timeout=self.timeout)
        except Exception as e:  # noqa: BLE001  (requests.RequestException, timeouts, etc.)
            raise BybitTransportError(f"{type(e).__name__}: {e}") from e
        status = getattr(r, "status_code", 200)
        rh = getattr(r, "headers", {}) or {}
        if status == 429 or status == 403:
            raise BybitTransportError(f"HTTP {status} (limite de IP/WAF)")
        if status >= 500:
            raise BybitTransportError(f"HTTP {status}")
        try:
            j = r.json()
        except Exception as e:  # noqa: BLE001
            raise BybitTransportError(f"resposta não-JSON (HTTP {status})") from e
        if str(rh.get("X-Bapi-Limit-Status", "1")) == "0":
            reset_ms = int(rh.get("X-Bapi-Limit-Reset-Timestamp", 0) or 0)
            wait = max(0.0, reset_ms / 1000 - self._time()) if reset_ms else 1.0
            self.limiter.penalize(min(wait, 10.0))
        if j.get("retCode", 0) != 0:
            raise BybitAPIError(int(j.get("retCode")), str(j.get("retMsg")), j)
        return j
