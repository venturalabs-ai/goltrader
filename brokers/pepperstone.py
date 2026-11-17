"""Pepperstone via cTrader Open API. Desligado por padrão.

A lógica do adapter (modo demo/live, mapeamento de ordens, kill switch) está pronta e testada com mock; o TRANSPORTE
(protobuf/WebSocket da cTrader Open API, OAuth2) ainda não foi implementado/validado em demo. Injete um `CTraderTransport`
ou implemente `default_transport()`. Enquanto isso o `readiness` é STUB: nenhuma ordem real é enviada sem transporte.
Demo: demo.ctraderapi.com; real: live.ctraderapi.com. Variáveis: PEPPERSTONE_CTRADER_CLIENT_ID/SECRET (+ token/conta).
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .base import BrokerAdapter, Mode, OrderRequest, OrderResult, Readiness

HOSTS = {Mode.TESTNET: "demo.ctraderapi.com", Mode.PAPER: "demo.ctraderapi.com", Mode.LIVE: "live.ctraderapi.com"}


class CTraderTransport(ABC):
    """Contrato mínimo com a cTrader Open API (implementação real fica fora do escopo desta entrega)."""

    @abstractmethod
    def bars(self, symbol: str, interval: str, limit: int) -> List[Dict[str, float]]: ...
    @abstractmethod
    def quote(self, symbol: str) -> Dict[str, Any]: ...
    @abstractmethod
    def account(self) -> Dict[str, Any]: ...
    @abstractmethod
    def positions(self) -> List[Dict[str, Any]]: ...
    @abstractmethod
    def new_order(self, symbol: str, side: str, volume: float, order_type: str, price: Optional[float], label: Optional[str],
                  reduce_only: bool) -> Dict[str, Any]: ...
    @abstractmethod
    def close_position(self, position_id: Any, volume: float) -> Dict[str, Any]: ...
    @abstractmethod
    def cancel_all(self, symbol: Optional[str]) -> Dict[str, Any]: ...


class PepperstoneAdapter(BrokerAdapter):
    name = "pepperstone"
    display_name = "Pepperstone"
    readiness = Readiness.STUB
    asset_classes = ("forex", "cfd")
    official_api = "cTrader Open API (OAuth2) ou MetaTrader 5 (pacote MetaTrader5, só Windows)"
    notes = "Lógica pronta + mock; transporte cTrader Open API pendente de validação em demo. MT5 só Windows."

    def __init__(self, mode: Mode = Mode.TESTNET, confirmation: Optional[str] = None,
                 transport: Optional[CTraderTransport] = None, environ: Optional[dict] = None):
        super().__init__(mode, confirmation)
        env = os.environ if environ is None else environ
        self.host = HOSTS[mode]
        self.client_id = env.get("PEPPERSTONE_CTRADER_CLIENT_ID", "")
        self.client_secret = env.get("PEPPERSTONE_CTRADER_CLIENT_SECRET", "")
        self._transport = transport

    @property
    def transport(self) -> CTraderTransport:
        if self._transport is None:
            raise NotImplementedError("Transporte cTrader Open API não implementado: injete um CTraderTransport (ver docs/PLANO-HIBRIDO.md).")
        return self._transport

    def klines(self, symbol: str, interval: str = "1", limit: int = 500):
        import pandas as pd
        df = pd.DataFrame(self.transport.bars(symbol, interval, limit))
        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df.set_index("timestamp").sort_index().astype(float)

    def ticker(self, symbol: str) -> Dict[str, Any]:
        return self.transport.quote(symbol)

    def balance(self) -> Dict[str, Any]:
        return self.transport.account()

    def positions(self) -> List[Dict[str, Any]]:
        return self.transport.positions()

    def _place_order(self, req: OrderRequest) -> OrderResult:
        r = self.transport.new_order(req.symbol, req.side, req.qty, req.order_type, req.price, req.client_id, req.reduce_only)
        return OrderResult(bool(r.get("ok", True)), str(r.get("order_id")), str(r.get("status", "NEW")), self.mode, r)

    def cancel_all(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        return self.transport.cancel_all(symbol)

    def close_all(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        out: Dict[str, Any] = {"cancel": None, "closed": [], "errors": []}
        if self.mode is Mode.PAPER:
            return out
        out["cancel"] = self.cancel_all(symbol)
        for p in self.positions():
            if symbol and p.get("symbol") != symbol:
                continue
            try:
                self.transport.close_position(p["position_id"], float(p["volume"]))
                out["closed"].append({"symbol": p.get("symbol"), "position_id": p["position_id"]})
            except Exception as e:  # noqa: BLE001
                out["errors"].append({"symbol": p.get("symbol"), "error": str(e)})
        return out
