"""IQ Option — NÃO OFICIAL. Desligado por padrão e com travas extras.

Não existe API pública oficial; usa a biblioteca não oficial `iqoptionapi` (injetável). Risco real de violar os Termos de
Uso, bloqueio de conta e quebra sem aviso. Por isso:
  - precisa de GT_BROKERS_ENABLED incluir "iqoption" E GOLTRADER_ENABLE_UNOFFICIAL=true;
  - só conta PRACTICE (TESTNET/PAPER). LIVE é recusado sempre neste adapter.
Opções binárias: buy → call, sell → put; `qty` = valor da entrada em USD; `extra["expiry_min"]` (padrão 1).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .base import BrokerAdapter, Mode, OrderRequest, OrderResult, Readiness


class UnofficialDisabled(PermissionError):
    pass


class IqoptionAdapter(BrokerAdapter):
    name = "iqoption"
    display_name = "IQ Option"
    readiness = Readiness.PARTIAL
    asset_classes = ("binary", "forex", "crypto")
    unofficial = True
    official_api = "SEM API oficial pública. Apenas bibliotecas não oficiais (ex.: iqoptionapi)"
    notes = "NÃO OFICIAL: risco de ToS/bloqueio. Só PRACTICE. Exige GOLTRADER_ENABLE_UNOFFICIAL=true."

    def __init__(self, mode: Mode = Mode.TESTNET, confirmation: Optional[str] = None, api: Any = None,
                 environ: Optional[dict] = None):
        env = os.environ if environ is None else environ
        if mode is Mode.LIVE:
            raise UnofficialDisabled("IQ Option (não oficial): conta real é recusada por este adapter.")
        super().__init__(mode, confirmation)
        if str(env.get("GOLTRADER_ENABLE_UNOFFICIAL", "")).strip().lower() not in ("1", "true", "yes", "sim"):
            raise UnofficialDisabled("IQ Option é não oficial: defina GOLTRADER_ENABLE_UNOFFICIAL=true para habilitar (por sua conta e risco).")
        self._env, self._api = env, api

    @property
    def api(self):
        if self._api is None:
            try:
                from iqoptionapi.stable_api import IQ_Option  # type: ignore
            except ImportError as e:
                raise RuntimeError("Biblioteca não oficial 'iqoptionapi' não instalada.") from e
            api = IQ_Option(self._env.get("IQOPTION_EMAIL", ""), self._env.get("IQOPTION_PASSWORD", ""))
            ok, reason = api.connect()
            if not ok:
                raise RuntimeError(f"IQ Option: login falhou ({reason})")
            self._api = api
        self._api.change_balance("PRACTICE")     # sempre conta de treino
        return self._api

    def klines(self, symbol: str, interval: str = "1", limit: int = 500):
        import pandas as pd
        import time
        secs = int(interval) * 60 if str(interval).isdigit() else 60
        rows = self.api.get_candles(symbol.upper(), secs, limit, time.time())
        df = pd.DataFrame(rows).rename(columns={"from": "timestamp", "max": "high", "min": "low"})
        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        return df.set_index("timestamp")[["open", "high", "low", "close", "volume"]].sort_index().astype(float)

    def ticker(self, symbol: str) -> Dict[str, Any]:
        rows = self.api.get_candles(symbol.upper(), 60, 1, __import__("time").time())
        return {"symbol": symbol.upper(), "last": rows[-1]["close"] if rows else None}

    def balance(self) -> Dict[str, Any]:
        return {"practice_balance": self.api.get_balance()}

    def positions(self) -> List[Dict[str, Any]]:
        return []     # binárias expiram sozinhas

    def _place_order(self, req: OrderRequest) -> OrderResult:
        if req.order_type != "market":
            raise ValueError("IQ Option: apenas entrada a mercado")
        ok, oid = self.api.buy(req.qty, req.symbol.upper(), "call" if req.side.lower() == "buy" else "put",
                               int(req.extra.get("expiry_min", 1)))
        return OrderResult(bool(ok), str(oid) if ok else None, "NEW" if ok else str(oid), self.mode, {})

    def cancel_all(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        return {"note": "binárias não são canceláveis"}

    def close_all(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        return {"closed": [], "note": "binárias expiram sozinhas; nada a fechar"}
