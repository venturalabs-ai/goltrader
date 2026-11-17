"""Adaptadores de corretora usados pelo gerenciador de risco (fechamento reduce-only, stop, saldo)."""
from __future__ import annotations

from typing import Dict, Optional, Tuple


class PaperBroker:
    """Posições de papel: nada vai à corretora."""

    async def close_position(self, symbol: str, side: str, qty: float) -> Dict:
        return {"ok": True, "paper": True}

    async def close_all(self) -> Dict:
        return {"ok": True, "paper": True, "closed": 0}

    async def move_stop(self, symbol: str, stop_price: float) -> Dict:
        return {"ok": True, "paper": True}

    async def get_equity(self) -> Optional[Tuple[float, str]]:
        return None


class BybitBroker:
    """Bybit V5 linear USDT. Testnet salvo BYBIT_TESTNET=false (herdado do cliente)."""

    def __init__(self, client_getter):
        self._get = client_getter

    @property
    def client(self):
        return self._get()

    async def get_equity(self) -> Optional[Tuple[float, str]]:
        c = self.client
        if not c.has_credentials:
            return None
        r = await c.get_wallet_balance()
        if r.get("retCode") != 0:
            return None
        try:
            val = float(r["result"]["list"][0]["totalEquity"])
        except (KeyError, IndexError, ValueError, TypeError):
            return None
        return val, ("BYBIT_TESTNET" if c.testnet else "BYBIT_REAL")

    @staticmethod
    def _opposite(side: str) -> str:
        return "Sell" if side.upper() in ("BUY", "LONG") else "Buy"

    async def close_position(self, symbol: str, side: str, qty: float) -> Dict:
        """Fecha `qty` com ordem a mercado reduce-only no lado oposto ao da posição (side = lado da posição)."""
        r = await self.client.place_order(
            category="linear", symbol=symbol, side=self._opposite(side), qty=f"{qty:.10g}",
            order_type="Market", reduce_only=True,
        )
        return {"ok": r.get("retCode") == 0, "retCode": r.get("retCode"), "retMsg": r.get("retMsg")}

    async def close_all(self) -> Dict:
        """Fecha TODAS as posições lineares USDT reportadas pela corretora (reduce-only, mercado)."""
        c = self.client
        pos = await c.get_positions("USDT")
        if pos.get("retCode") != 0:
            return {"ok": False, "retMsg": pos.get("retMsg"), "closed": 0}
        closed, failed = 0, []
        for p in pos.get("result", {}).get("list", []):
            try:
                size = float(p.get("size") or 0)
            except ValueError:
                size = 0
            if size <= 0:
                continue
            r = await c.place_order(
                category="linear", symbol=p["symbol"], side=self._opposite("BUY" if p.get("side") == "Buy" else "SELL"),
                qty=str(p["size"]), order_type="Market", reduce_only=True,
            )
            if r.get("retCode") == 0:
                closed += 1
            else:
                failed.append(f"{p['symbol']}: {r.get('retMsg')}")
        return {"ok": not failed, "closed": closed, "failed": failed}

    async def move_stop(self, symbol: str, stop_price: float) -> Dict:
        r = await self.client.set_trading_stop(symbol, f"{stop_price:.2f}")
        return {"ok": r.get("retCode") in (0, 34040), "retCode": r.get("retCode"), "retMsg": r.get("retMsg")}  # 34040 = not modified
