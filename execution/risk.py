"""Limites de risco pré-ordem (máx. posição, perda diária, ordens/min). Ordens reduce-only sempre passam
(fechar risco nunca pode ser bloqueado pelo próprio limite)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from brokers.base import OrderRequest
from core.ratelimit import SlidingWindow


class RiskRejected(RuntimeError):
    def __init__(self, rule: str, message: str):
        super().__init__(f"[{rule}] {message}")
        self.rule = rule


@dataclass
class RiskLimits:
    max_position_usd: float = 1000.0     # exposição líquida máxima por símbolo (USDT)
    max_order_usd: float = 500.0         # notional máximo por ordem
    max_daily_loss_usd: float = 100.0    # perda diária (equity do início do dia − equity atual) que trava tudo
    max_orders_per_min: int = 20
    kill_on_daily_loss: bool = True      # ao estourar a perda diária, aciona kill switch (fecha tudo reduce-only)

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "RiskLimits":
        env = os.environ if environ is None else environ
        d = cls()
        return cls(
            max_position_usd=float(env.get("GOLTRADER_MAX_POSITION_USD", d.max_position_usd)),
            max_order_usd=float(env.get("GOLTRADER_MAX_ORDER_USD", d.max_order_usd)),
            max_daily_loss_usd=float(env.get("GOLTRADER_MAX_DAILY_LOSS_USD", d.max_daily_loss_usd)),
            max_orders_per_min=int(env.get("GOLTRADER_MAX_ORDERS_PER_MIN", d.max_orders_per_min)),
            kill_on_daily_loss=str(env.get("GOLTRADER_KILL_ON_DAILY_LOSS", "true")).lower() in ("1", "true", "yes", "sim"),
        )


class RiskGuard:
    def __init__(self, limits: RiskLimits, clock: Callable[[], float] = None):
        import time
        self.limits = limits
        self._window = SlidingWindow(limits.max_orders_per_min, 60.0, clock or time.monotonic)

    def check(self, req: OrderRequest, price: float, signed_position_qty: float, daily_loss_usd: float) -> None:
        if req.qty <= 0 or price <= 0:
            raise RiskRejected("dados", "qty e preço precisam ser positivos")
        if req.reduce_only:
            return
        L = self.limits
        if daily_loss_usd >= L.max_daily_loss_usd:
            raise RiskRejected("perda_diaria", f"perda do dia {daily_loss_usd:.2f} ≥ limite {L.max_daily_loss_usd:.2f} USDT")
        notional = req.qty * price
        if notional > L.max_order_usd:
            raise RiskRejected("max_ordem", f"notional {notional:.2f} > {L.max_order_usd:.2f} USDT")
        delta = req.qty if req.side.lower() == "buy" else -req.qty
        new_pos = signed_position_qty + delta
        if abs(new_pos) * price > L.max_position_usd and abs(new_pos) > abs(signed_position_qty):
            raise RiskRejected("max_posicao", f"posição resultante {abs(new_pos) * price:.2f} > {L.max_position_usd:.2f} USDT")
        if not self._window.allow():
            raise RiskRejected("ordens_por_min", f"mais de {L.max_orders_per_min} ordens/min")

    def record_order(self) -> None:
        self._window.record()
