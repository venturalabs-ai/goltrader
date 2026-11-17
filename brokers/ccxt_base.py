"""Base para exchanges via ccxt (Binance, OKX, Coinbase, Kraken). Mesma interface do BrokerAdapter.

- `ccxt` é importado só quando preciso (dependência opcional: brokers/requirements-brokers.txt).
- Testes injetam `exchange` falso; nenhum acesso à rede.
- TESTNET usa `set_sandbox_mode(True)` quando a exchange tem sandbox; senão o adapter recusa TESTNET (use PAPER).
- Credenciais só do ambiente (nomes em `credential_env`).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import BrokerAdapter, Mode, OrderRequest, OrderResult, Readiness

INTERVALS = {"1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m", "60": "1h", "120": "2h", "240": "4h", "D": "1d", "W": "1w"}


class SandboxUnavailable(PermissionError):
    """A exchange não tem sandbox utilizável: só PAPER (ou LIVE com todas as travas)."""


class CcxtAdapter(BrokerAdapter):
    readiness = Readiness.PARTIAL      # mapeamento pronto; testado só com mock (sem E2E em sandbox)
    asset_classes = ("crypto",)
    ccxt_id = ""
    supports_testnet = True
    default_type = "spot"
    credential_env: Dict[str, str] = {}   # campo ccxt -> variável de ambiente

    def __init__(self, mode: Mode = Mode.TESTNET, confirmation: Optional[str] = None, exchange: Any = None,
                 environ: Optional[dict] = None):
        super().__init__(mode, confirmation)
        if mode is Mode.TESTNET and not self.supports_testnet:
            raise SandboxUnavailable(f"{self.display_name} não tem sandbox utilizável: use PAPER ou LIVE (com as travas).")
        self._env = os.environ if environ is None else environ
        self._exchange = exchange

    @property
    def exchange(self):
        if self._exchange is None:
            try:
                import ccxt  # type: ignore
            except ImportError as e:
                raise RuntimeError("Instale ccxt: pip install -r brokers/requirements-brokers.txt") from e
            cfg: Dict[str, Any] = {"enableRateLimit": True, "options": {"defaultType": self.default_type}}
            for field, var in self.credential_env.items():
                cfg[field] = self._env.get(var, "")
            ex = getattr(ccxt, self.ccxt_id)(cfg)
            if self.mode is Mode.TESTNET:
                ex.set_sandbox_mode(True)
            self._exchange = ex
        return self._exchange

    @staticmethod
    def norm_symbol(symbol: str) -> str:
        s = symbol.upper()
        if "/" not in s:
            for q in ("USDT", "USDC", "USD", "BRL", "EUR"):
                if s.endswith(q) and len(s) > len(q):
                    return f"{s[:-len(q)]}/{q}"
        return s

    def klines(self, symbol: str, interval: str = "1", limit: int = 500) -> pd.DataFrame:
        rows = self.exchange.fetch_ohlcv(self.norm_symbol(symbol), INTERVALS.get(str(interval), str(interval)), limit=limit)
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True)
        return df.set_index("timestamp").sort_index().astype(float)

    def ticker(self, symbol: str) -> Dict[str, Any]:
        return self.exchange.fetch_ticker(self.norm_symbol(symbol))

    def balance(self) -> Dict[str, Any]:
        return self.exchange.fetch_balance()

    def positions(self) -> List[Dict[str, Any]]:
        if not self.exchange.has.get("fetchPositions"):
            return []
        return [p for p in self.exchange.fetch_positions() if abs(float(p.get("contracts") or 0)) > 0]

    def _place_order(self, req: OrderRequest) -> OrderResult:
        params: Dict[str, Any] = dict(req.extra)
        if req.client_id:
            params["clientOrderId"] = req.client_id
        if req.reduce_only:
            params["reduceOnly"] = True
        o = self.exchange.create_order(self.norm_symbol(req.symbol), req.order_type, req.side, req.qty, req.price, params)
        return OrderResult(True, str(o.get("id")), str(o.get("status") or "open"), self.mode, o)

    def cancel_all(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        r = self.exchange.cancel_all_orders(self.norm_symbol(symbol) if symbol else None)
        return {"result": r}

    def close_all(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        out: Dict[str, Any] = {"cancel": self.cancel_all(symbol) if self.mode is not Mode.PAPER else None, "closed": [], "errors": []}
        if self.mode is Mode.PAPER:
            return out
        for p in self.positions():
            sym = p["symbol"]
            if symbol and sym != self.norm_symbol(symbol):
                continue
            qty = abs(float(p.get("contracts") or 0))
            side = "sell" if p.get("side") in ("long", "buy") else "buy"
            try:
                o = self.exchange.create_order(sym, "market", side, qty, None, {"reduceOnly": True})
                out["closed"].append({"symbol": sym, "order_id": o.get("id")})
            except Exception as e:  # noqa: BLE001
                out["errors"].append({"symbol": sym, "error": str(e)})
        return out
