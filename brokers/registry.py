"""Registro de adapters. Todos DESLIGADOS por padrão, exceto o que estiver em GT_BROKERS_ENABLED
(lista separada por vírgula; padrão: "bybit"). Modo padrão: testnet."""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Type

from .base import BrokerAdapter, Mode
from .binance import BinanceAdapter
from .bybit import BybitAdapter
from .coinbase import CoinbaseAdapter
from .iqoption import IqoptionAdapter
from .ibkr import IbkrAdapter
from .kraken import KrakenAdapter
from .okx import OkxAdapter
from .pepperstone import PepperstoneAdapter

ALL: Dict[str, Type[BrokerAdapter]] = {c.name: c for c in (
    BybitAdapter, BinanceAdapter, OkxAdapter, CoinbaseAdapter, KrakenAdapter, IbkrAdapter, PepperstoneAdapter, IqoptionAdapter)}


def enabled_names() -> List[str]:
    raw = os.getenv("GT_BROKERS_ENABLED", "bybit")
    return [n.strip().lower() for n in raw.split(",") if n.strip()]


def catalog() -> List[dict]:
    en = set(enabled_names())
    out = []
    for name, cls in ALL.items():
        out.append({"name": name, "display_name": cls.display_name, "readiness": cls.readiness.value,
                    "asset_classes": list(cls.asset_classes), "official_api": cls.official_api,
                    "enabled": name in en, "notes": cls.notes, "unofficial": cls.unofficial})
    return out


def get_broker(name: str, mode: Mode = Mode.TESTNET, confirmation: Optional[str] = None, **kw) -> BrokerAdapter:
    name = name.lower()
    if name not in ALL:
        raise KeyError(f"Corretora desconhecida: {name}")
    if name not in enabled_names():
        raise PermissionError(f"Adapter '{name}' está desligado. Habilite em GT_BROKERS_ENABLED.")
    return ALL[name](mode=mode, confirmation=confirmation, **kw)
