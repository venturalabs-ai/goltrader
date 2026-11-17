"""Interactive Brokers via ib_insync (TWS / IB Gateway). Desligado por padrão.

Portas: paper = 7497 (TWS) / 4002 (Gateway); real = 7496 / 4001. TESTNET só conecta em porta paper; LIVE só em porta real
(e exige todas as travas). `ib` e `lib` (módulo ib_insync) são injetáveis para testes sem rede/sem TWS.
Contrato: por padrão Stock(SMART, USD); use extra={"sec_type":"CASH"} (forex) ou {"exchange":"BOVESPA","currency":"BRL"} (B3).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import BrokerAdapter, Mode, OrderRequest, OrderResult, Readiness

PAPER_PORTS = {7497, 4002}
LIVE_PORTS = {7496, 4001}
BAR = {"1": "1 min", "5": "5 mins", "15": "15 mins", "60": "1 hour", "D": "1 day"}


class IbkrPortMismatch(PermissionError):
    pass


class IbkrAdapter(BrokerAdapter):
    name = "ibkr"
    display_name = "Interactive Brokers"
    readiness = Readiness.PARTIAL
    asset_classes = ("stocks", "options", "futures", "forex")
    official_api = "TWS API / IB Gateway (ib_insync/ib_async) ou Client Portal Web API"
    notes = "Via ib_insync; paper em 7497/4002. Inclui B3 via IBKR. Testado só com mock; exige TWS/Gateway ligado."

    def __init__(self, mode: Mode = Mode.TESTNET, confirmation: Optional[str] = None, ib: Any = None, lib: Any = None,
                 environ: Optional[dict] = None):
        super().__init__(mode, confirmation)
        env = os.environ if environ is None else environ
        self.host = env.get("IBKR_HOST", "127.0.0.1")
        self.port = int(env.get("IBKR_PORT", "7497"))
        self.client_id = int(env.get("IBKR_CLIENT_ID", "1"))
        if mode is Mode.TESTNET and self.port not in PAPER_PORTS:
            raise IbkrPortMismatch(f"TESTNET exige porta paper {sorted(PAPER_PORTS)}; IBKR_PORT={self.port}")
        if mode is Mode.LIVE and self.port not in LIVE_PORTS:
            raise IbkrPortMismatch(f"LIVE exige porta real {sorted(LIVE_PORTS)}; IBKR_PORT={self.port}")
        self._ib, self._lib = ib, lib

    @property
    def lib(self):
        if self._lib is None:
            try:
                import ib_insync as lib  # type: ignore
            except ImportError:
                try:
                    import ib_async as lib  # type: ignore
                except ImportError as e:
                    raise RuntimeError("Instale ib_insync: pip install -r brokers/requirements-brokers.txt") from e
            self._lib = lib
        return self._lib

    @property
    def ib(self):
        if self._ib is None:
            self._ib = self.lib.IB()
        if not self._ib.isConnected():
            self._ib.connect(self.host, self.port, clientId=self.client_id, readonly=self.mode is Mode.PAPER)
        return self._ib

    def contract(self, symbol: str, extra: Optional[dict] = None):
        extra = extra or {}
        if extra.get("sec_type") == "CASH":
            return self.lib.Forex(symbol.upper())
        return self.lib.Stock(symbol.upper(), extra.get("exchange", "SMART"), extra.get("currency", "USD"))

    def klines(self, symbol: str, interval: str = "1", limit: int = 500) -> pd.DataFrame:
        bars = self.ib.reqHistoricalData(self.contract(symbol), endDateTime="", durationStr="1 D",
                                         barSizeSetting=BAR.get(str(interval), "1 min"), whatToShow="TRADES",
                                         useRTH=False, formatDate=2)
        df = pd.DataFrame([{"timestamp": b.date, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
                           for b in bars][-limit:])
        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df.set_index("timestamp").sort_index().astype(float)

    def ticker(self, symbol: str) -> Dict[str, Any]:
        t = self.ib.reqMktData(self.contract(symbol), "", True, False)
        return {"symbol": symbol.upper(), "last": getattr(t, "last", None), "bid": getattr(t, "bid", None), "ask": getattr(t, "ask", None)}

    def balance(self) -> Dict[str, Any]:
        return {r.tag: r.value for r in self.ib.accountSummary() if r.tag in ("NetLiquidation", "TotalCashValue", "AvailableFunds")}

    def positions(self) -> List[Dict[str, Any]]:
        return [{"symbol": p.contract.symbol, "position": float(p.position), "avg_cost": float(p.avgCost)} for p in self.ib.positions()
                if float(p.position) != 0]

    def _place_order(self, req: OrderRequest) -> OrderResult:
        if req.order_type == "limit":
            if not req.price:
                raise ValueError("ordem limit exige price")
            order = self.lib.LimitOrder(req.side.upper(), req.qty, req.price)
        else:
            order = self.lib.MarketOrder(req.side.upper(), req.qty)
        if req.client_id:
            order.orderRef = req.client_id      # rastreio/idempotência no nosso lado
        trade = self.ib.placeOrder(self.contract(req.symbol, req.extra), order)
        st = getattr(getattr(trade, "orderStatus", None), "status", "Submitted")
        return OrderResult(True, str(getattr(getattr(trade, "order", None), "orderId", "")), st, self.mode, {})

    def cancel_all(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        self.ib.reqGlobalCancel()      # IBKR não filtra por símbolo no cancel global
        return {"scope": "global"}

    def close_all(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        out: Dict[str, Any] = {"cancel": None, "closed": [], "errors": []}
        if self.mode is Mode.PAPER:
            return out
        out["cancel"] = self.cancel_all()
        for p in self.ib.positions():
            sym, qty = p.contract.symbol, float(p.position)
            if qty == 0 or (symbol and sym != symbol.upper()):
                continue
            try:
                self.ib.placeOrder(p.contract, self.lib.MarketOrder("SELL" if qty > 0 else "BUY", abs(qty)))
                out["closed"].append({"symbol": sym, "qty": abs(qty)})
            except Exception as e:  # noqa: BLE001
                out["errors"].append({"symbol": sym, "error": str(e)})
        return out
