"""Modelos de dados do Pulse (telemetria e requisições)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


@dataclass
class OrderBlock:
    id: str
    symbol: str
    side: str  # "BUY" (Bullish OB) | "SELL" (Bearish OB)
    top: float
    bottom: float
    status: str  # "FORMING" | "CONFIRMED"
    retested: bool
    distance_pct: float
    timestamp: int


@dataclass
class BookWall:
    """Volumes em US$ milhões dentro da faixa de profundidade realmente disponível (range_pct)."""

    symbol: str
    bid_vol_usd_m: float
    ask_vol_usd_m: float
    biggest_bid_price: float
    biggest_bid_size_usd_m: float
    biggest_ask_price: float
    biggest_ask_size_usd_m: float
    imbalance_ratio: float
    range_pct: float  # faixa (±%) efetivamente coberta pelo book (máx. 0.5)


@dataclass
class DerivativesPulse:
    symbol: str
    last_price: float
    change_24h: Optional[float]
    open_interest_usd_m: Optional[float]
    oi_change_pct: Optional[float]  # variação do OI na janela oi_window_s (não é 24h)
    oi_window_s: Optional[int]
    funding_rate_pct: Optional[float]  # % por período de funding
    basis_pct: Optional[float]  # (mark-index)/index em %
    pcr_oi: Optional[float]
    max_pain: Optional[float]
    atm_iv: Optional[float]  # % a.a.
    buildup_status: str  # LONG BUILDUP | SHORT BUILDUP | SHORT COVERING | LONG UNWINDING | SEM DADOS


Venue = Literal["BYBIT_PERP", "BYBIT_SPOT", "BYBIT_OPT", "IQ_BINARY", "IQ_DIGITAL"]


class SmartOrderRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9\-_.]+$")
    venue: Venue
    side: Literal["BUY", "SELL"]
    amount: float = Field(gt=0, le=1_000_000)
    leverage: Optional[int] = Field(10, ge=1, le=125)
    expiration_min: Optional[int] = Field(1, ge=1, le=60)
    price: Optional[float] = Field(None, gt=0)  # limite (obrigatório na prática para opções)
    take_profit: Optional[float] = Field(None, gt=0)
    stop_loss: Optional[float] = Field(None, gt=0)

    @field_validator("side", "venue", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.upper() if isinstance(v, str) else v


class ToggleModeBody(BaseModel):
    confirm: Optional[str] = None


class KillSwitchBody(BaseModel):
    active: bool = True
