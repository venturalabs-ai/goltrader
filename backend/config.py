"""Leitura de configuração por variáveis de ambiente (lidas a cada chamada, facilita testes)."""
from __future__ import annotations

import os
from typing import List


def env_str(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return default if v is None else v.strip()


def env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "sim")


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, "") or default))
    except ValueError:
        return default


def api_token() -> str:
    return env_str("GOLTRADER_API_TOKEN")


def cors_origins() -> List[str]:
    raw = env_str("GOLTRADER_CORS_ORIGINS", "http://localhost:5173")
    origins = [o.strip() for o in raw.split(",") if o.strip() and o.strip() != "*"]
    return origins or ["http://localhost:5173"]


def data_source_name() -> str:
    """'BYBIT' (feed público real) ou 'SIMULADO'."""
    return "BYBIT" if env_str("GOLTRADER_DATA_SOURCE", "simulado").lower() == "bybit" else "SIMULADO"
