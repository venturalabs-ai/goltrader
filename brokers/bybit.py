"""Adapter Bybit V5: spot + perpétuos lineares USDT (ordens) e opções (apenas dados públicos).

Segurança: Testnet por padrão; LIVE exige confirmação "CONFIRMAR" + GOLTRADER_ALLOW_LIVE=true + BYBIT_TESTNET=false
(ver core/safety.py). Kill switch checado em toda ordem (BrokerAdapter.place_order).
Credenciais só do ambiente: BYBIT_TESTNET_API_KEY/SECRET, BYBIT_REAL_API_KEY/SECRET (ou legado BYBIT_API_KEY/SECRET).

Ordens são idempotentes via `orderLinkId` (gerado se não vier em `client_id`): reenvio após timeout não duplica;
retCode 110072 (link duplicado) vira consulta da ordem existente.
"""
from __future__ import annotations

import os
import re
import secrets
import time
from decimal import ROUND_DOWN, Decimal
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import BrokerAdapter, Mode, OrderRequest, OrderResult, Readiness
from .bybit_client import (LIVE_REST, RET_DUPLICATE_LINK, TESTNET_REST, BybitAPIError, BybitRest)

CATEGORIES = ("spot", "linear", "option")
TRADABLE = ("spot", "linear")
LINK_RE = re.compile(r"^[A-Za-z0-9_-]{1,36}$")
TERMINAL = {"Filled", "Cancelled", "Rejected", "PartiallyFilledCanceled", "Deactivated", "Expired"}


class OptionTradingDisabled(PermissionError):
    """Opções: só dados públicos nesta versão."""


class OrderRejected(RuntimeError):
    pass


def new_link_id(prefix: str = "gt") -> str:
    return f"{prefix}-{int(time.time() * 1000)}-{secrets.token_hex(4)}"[:36]


def fmt_decimal(d: Decimal) -> str:
    s = format(d.normalize(), "f")
    return s if "." not in s else s.rstrip("0").rstrip(".")


def credentials(mode: Mode, environ=None) -> tuple:
    env = os.environ if environ is None else environ
    prefix = "BYBIT_REAL" if mode is Mode.LIVE else "BYBIT_TESTNET"
    key = env.get(f"{prefix}_API_KEY") or env.get("BYBIT_API_KEY", "")
    sec = env.get(f"{prefix}_API_SECRET") or env.get("BYBIT_API_SECRET", "")
    return key, sec


def make_rest(mode: Mode, session=None, **kw) -> BybitRest:
    key, sec = credentials(mode)
    return BybitRest(LIVE_REST if mode is Mode.LIVE else TESTNET_REST, key, sec, session=session, **kw)


class BybitAdapter(BrokerAdapter):
    name = "bybit"
    display_name = "Bybit"
    readiness = Readiness.PARTIAL   # READY só após E2E na Testnet com chaves reais de Testnet
    asset_classes = ("crypto",)
    official_api = "https://bybit-exchange.github.io/docs/v5/intro"
    notes = "Spot + perp linear USDT (ordens); opções só dados públicos. Testnet padrão; real só com 'CONFIRMAR'."

    def __init__(self, mode: Mode = Mode.TESTNET, confirmation: Optional[str] = None, category: str = "spot",
                 rest: Optional[BybitRest] = None, session=None):
        super().__init__(mode, confirmation)
        if category not in CATEGORIES:
            raise ValueError(f"category deve ser uma de {CATEGORIES}")
        if mode is Mode.LIVE:
            from core.safety import assert_live_allowed
            assert_live_allowed()   # exige também BYBIT_TESTNET=false
        self.category = category
        self.rest = rest or make_rest(mode, session=session)
        self._instruments: Dict[str, Dict[str, Any]] = {}

    # ---------------- público ----------------
    def klines(self, symbol: str, interval: str = "1", limit: int = 500) -> pd.DataFrame:
        j = self.rest.request("GET", "/v5/market/kline", {"category": self.category, "symbol": symbol.upper(),
                              "interval": str(interval), "limit": min(limit, 1000)}, auth=False)
        df = pd.DataFrame(j["result"]["list"], columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True)
        return df.set_index("timestamp").sort_index().astype(float)

    def ticker(self, symbol: str) -> Dict[str, Any]:
        return self.rest.request("GET", "/v5/market/tickers", {"category": self.category, "symbol": symbol.upper()},
                                 auth=False).get("result", {})

    def last_price(self, symbol: str) -> float:
        lst = self.ticker(symbol).get("list") or []
        if not lst:
            raise RuntimeError(f"sem ticker para {symbol}")
        return float(lst[0]["lastPrice"])

    def instrument(self, symbol: str) -> Dict[str, Any]:
        symbol = symbol.upper()
        if symbol not in self._instruments:
            j = self.rest.request("GET", "/v5/market/instruments-info", {"category": self.category, "symbol": symbol}, auth=False)
            lst = j["result"].get("list") or []
            if not lst:
                raise ValueError(f"instrumento desconhecido: {symbol}")
            self._instruments[symbol] = lst[0]
        return self._instruments[symbol]

    def normalize_qty(self, symbol: str, qty: float) -> str:
        lot = self.instrument(symbol).get("lotSizeFilter", {})
        step = Decimal(str(lot.get("qtyStep") or lot.get("basePrecision") or "0.000001"))
        mn = Decimal(str(lot.get("minOrderQty") or "0"))
        d = (Decimal(str(qty)) / step).to_integral_value(rounding=ROUND_DOWN) * step
        if d < mn or d <= 0:
            raise OrderRejected(f"qty {qty} abaixo do mínimo {mn} para {symbol}")
        mx = lot.get("maxOrderQty")
        if mx and d > Decimal(str(mx)):
            raise OrderRejected(f"qty {qty} acima do máximo {mx} para {symbol}")
        return fmt_decimal(d)

    def normalize_price(self, symbol: str, price: float) -> str:
        tick = Decimal(str(self.instrument(symbol).get("priceFilter", {}).get("tickSize") or "0.01"))
        return fmt_decimal((Decimal(str(price)) / tick).to_integral_value(rounding=ROUND_DOWN) * tick)

    # ---- opções: apenas leitura pública ----
    def option_instruments(self, base_coin: str = "BTC", limit: int = 1000) -> List[Dict[str, Any]]:
        j = self.rest.request("GET", "/v5/market/instruments-info", {"category": "option", "baseCoin": base_coin.upper(),
                              "limit": limit}, auth=False)
        return j["result"].get("list", [])

    def option_tickers(self, base_coin: str = "BTC", exp_date: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"category": "option", "baseCoin": base_coin.upper()}
        if exp_date:
            params["expDate"] = exp_date
        return self.rest.request("GET", "/v5/market/tickers", params, auth=False)["result"].get("list", [])

    # ---------------- privado ----------------
    def balance(self) -> Dict[str, Any]:
        return self.rest.request("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})

    def positions(self) -> List[Dict[str, Any]]:
        if self.category != "linear":
            return []
        out, cursor = [], None
        while True:
            params = {"category": "linear", "settleCoin": "USDT", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            res = self.rest.request("GET", "/v5/position/list", params)["result"]
            out += [p for p in res.get("list", []) if float(p.get("size") or 0) > 0]
            cursor = res.get("nextPageCursor")
            if not cursor:
                return out

    def open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"category": self.category, "limit": 50}
        if symbol:
            params["symbol"] = symbol.upper()
        elif self.category == "linear":
            params["settleCoin"] = "USDT"
        out, cursor = [], None
        while True:
            if cursor:
                params["cursor"] = cursor
            res = self.rest.request("GET", "/v5/order/realtime", params)["result"]
            out += res.get("list", [])
            cursor = res.get("nextPageCursor")
            if not cursor:
                return out

    def get_order(self, order_link_id: str, symbol: Optional[str] = None) -> Optional[Dict[str, Any]]:
        params: Dict[str, Any] = {"category": self.category, "orderLinkId": order_link_id}
        if symbol:
            params["symbol"] = symbol.upper()
        elif self.category == "linear":
            params["settleCoin"] = "USDT"
        lst = self.rest.request("GET", "/v5/order/realtime", params)["result"].get("list", [])
        if not lst:   # já encerrada (Filled/Cancelled): consulta o histórico
            lst = self.rest.request("GET", "/v5/order/history", params)["result"].get("list", [])
        return lst[0] if lst else None

    def executions(self, start_ms: Optional[int] = None, limit: int = 100) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"category": self.category, "limit": limit}
        if start_ms:
            params["startTime"] = start_ms
        return self.rest.request("GET", "/v5/execution/list", params)["result"].get("list", [])

    def build_order_body(self, req: OrderRequest) -> Dict[str, Any]:
        if self.category not in TRADABLE:
            raise OptionTradingDisabled("Opções Bybit: somente dados públicos (ordens desabilitadas).")
        link = req.client_id or new_link_id()
        if not LINK_RE.match(link):
            raise ValueError("client_id/orderLinkId inválido (1-36 caracteres [A-Za-z0-9_-])")
        otype = req.order_type.capitalize()
        body: Dict[str, Any] = {"category": self.category, "symbol": req.symbol.upper(), "side": req.side.capitalize(),
                                "orderType": otype, "qty": self.normalize_qty(req.symbol, req.qty), "orderLinkId": link,
                                "timeInForce": "IOC" if otype == "Market" else "GTC"}
        if self.category == "spot" and otype == "Market":
            body["marketUnit"] = "baseCoin"    # qty em moeda base (padrão da Bybit seria quote em compras)
        if otype == "Limit":
            if not req.price:
                raise ValueError("ordem limit exige price")
            body["price"] = self.normalize_price(req.symbol, req.price)
        if req.reduce_only:
            if self.category != "linear":
                raise ValueError("reduce_only só existe em perp linear")
            body["reduceOnly"] = True
        for k in ("timeInForce", "positionIdx", "takeProfit", "stopLoss", "tpslMode", "triggerPrice"):
            if k in req.extra:
                body[k] = req.extra[k]
        return body

    def _place_order(self, req: OrderRequest) -> OrderResult:
        body = self.build_order_body(req)
        link = body["orderLinkId"]
        try:
            j = self.rest.request("POST", "/v5/order/create", body=body, idempotent=True)
        except BybitAPIError as e:
            if e.ret_code == RET_DUPLICATE_LINK:      # já aceita antes (ex.: timeout + retry): devolve a existente
                o = self.get_order(link, body["symbol"])
                if o:
                    return OrderResult(True, o.get("orderId"), o.get("orderStatus", "NEW"), self.mode, {"duplicate": True, "order": o})
            return OrderResult(False, None, f"{e.ret_code}:{e.ret_msg}", self.mode, e.payload)
        res = j.get("result") or {}
        return OrderResult(True, res.get("orderId"), "NEW", self.mode, {**j, "orderLinkId": link})

    def cancel_all(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        if self.category not in TRADABLE:
            return {}
        body: Dict[str, Any] = {"category": self.category}
        if symbol:
            body["symbol"] = symbol.upper()
        elif self.category == "linear":
            body["settleCoin"] = "USDT"
        return self.rest.request("POST", "/v5/order/cancel-all", body=body, idempotent=True)

    def close_all(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Kill switch: cancela ordens abertas e fecha posições lineares com market reduce-only.
        Chama `_place_order` direto (não `place_order`) de propósito: o kill switch ativo bloqueia ordens normais,
        mas o fechamento reduce-only precisa passar. Spot: só cancela ordens (não vende saldo)."""
        out: Dict[str, Any] = {"cancel": None, "closed": [], "errors": []}
        if self.mode is Mode.PAPER or self.category != "linear":
            if self.category == "spot" and self.mode is not Mode.PAPER:
                out["cancel"] = self.cancel_all(symbol)
            return out
        out["cancel"] = self.cancel_all(symbol)
        for pos in self.positions():
            if symbol and pos["symbol"] != symbol.upper():
                continue
            side = "sell" if pos.get("side") == "Buy" else "buy"
            extra = {"positionIdx": int(pos.get("positionIdx") or 0)}
            r = self._place_order(OrderRequest(pos["symbol"], side, float(pos["size"]), "market", reduce_only=True,
                                               client_id=new_link_id("gtkill"), extra=extra))
            (out["closed"] if r.ok else out["errors"]).append({"symbol": pos["symbol"], "result": r.status, "order_id": r.order_id})
        return out
