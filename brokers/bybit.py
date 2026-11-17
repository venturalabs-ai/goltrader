"""Adapter Bybit (V5). Testnet por padrão. Consultas e ordens via requests assinadas (HMAC),
reaproveitando o conector existente para klines. Credenciais só do ambiente.

Variáveis: BYBIT_TESTNET_API_KEY/SECRET, BYBIT_REAL_API_KEY/SECRET (ver .env.example).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from .base import BrokerAdapter, Mode, OrderRequest, OrderResult, Readiness

URLS = {Mode.TESTNET: "https://api-testnet.bybit.com", Mode.LIVE: "https://api.bybit.com", Mode.PAPER: "https://api-testnet.bybit.com"}


class BybitAdapter(BrokerAdapter):
    name = "bybit"
    display_name = "Bybit"
    readiness = Readiness.PARTIAL   # sobe para READY após testes E2E na Testnet (ver plano)
    asset_classes = ("crypto",)
    official_api = "https://bybit-exchange.github.io/docs/v5/intro (pybit / ccxt: bybit)"
    notes = "Testnet padrão; real só com confirmação 'CONFIRMAR' e kill switch."

    def __init__(self, mode: Mode = Mode.TESTNET, confirmation: Optional[str] = None, category: str = "spot"):
        super().__init__(mode, confirmation)
        self.category = category
        self.base = URLS[mode]
        prefix = "BYBIT_REAL" if mode is Mode.LIVE else "BYBIT_TESTNET"
        self._key = os.getenv(f"{prefix}_API_KEY", "")
        self._secret = os.getenv(f"{prefix}_API_SECRET", "")

    # --- público ---
    def klines(self, symbol: str, interval: str = "1", limit: int = 500) -> pd.DataFrame:
        r = requests.get(f"{self.base}/v5/market/kline", params={"category": self.category, "symbol": symbol.upper(),
                         "interval": str(interval), "limit": min(limit, 1000)}, timeout=15)
        r.raise_for_status()
        j = r.json()
        if j.get("retCode") != 0:
            raise RuntimeError(f"Bybit: {j.get('retMsg')}")
        df = pd.DataFrame(j["result"]["list"], columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True)
        df = df.set_index("timestamp").sort_index().astype(float)
        return df

    def ticker(self, symbol: str) -> Dict[str, Any]:
        r = requests.get(f"{self.base}/v5/market/tickers", params={"category": self.category, "symbol": symbol.upper()}, timeout=15)
        r.raise_for_status()
        return r.json().get("result", {})

    # --- privado ---
    def _signed(self, method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None) -> Dict[str, Any]:
        if not (self._key and self._secret):
            raise RuntimeError("Credenciais Bybit ausentes no ambiente (.env).")
        ts, rw = str(int(time.time() * 1000)), "5000"
        if method == "GET":
            qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
            pre, payload, url = f"{ts}{self._key}{rw}{qs}", None, f"{self.base}{path}?{qs}"
        else:
            payload = json.dumps(body or {})
            pre, url = f"{ts}{self._key}{rw}{payload}", f"{self.base}{path}"
        sig = hmac.new(self._secret.encode(), pre.encode(), hashlib.sha256).hexdigest()
        h = {"X-BAPI-API-KEY": self._key, "X-BAPI-SIGN": sig, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": rw, "Content-Type": "application/json"}
        r = requests.request(method, url, headers=h, data=payload, timeout=15)
        return r.json()

    def balance(self) -> Dict[str, Any]:
        return self._signed("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})

    def positions(self) -> List[Dict[str, Any]]:
        if self.category == "spot":
            return []
        return self._signed("GET", "/v5/position/list", {"category": self.category, "settleCoin": "USDT"}).get("result", {}).get("list", [])

    def _place_order(self, req: OrderRequest) -> OrderResult:
        body = {"category": self.category, "symbol": req.symbol.upper(), "side": req.side.capitalize(),
                "orderType": req.order_type.capitalize(), "qty": str(req.qty)}
        if req.order_type == "limit" and req.price:
            body["price"] = str(req.price)
        if req.reduce_only:
            body["reduceOnly"] = True
        j = self._signed("POST", "/v5/order/create", body=body)
        ok = j.get("retCode") == 0
        return OrderResult(ok, (j.get("result") or {}).get("orderId"), "NEW" if ok else str(j.get("retMsg")), self.mode, j)

    def cancel_all(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        body = {"category": self.category}
        if symbol:
            body["symbol"] = symbol.upper()
        return self._signed("POST", "/v5/order/cancel-all", body=body)
