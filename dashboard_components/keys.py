"""Resolucao de credenciais Bybit SOMENTE via .env / variaveis de ambiente.

Nunca imprime, loga ou exibe valores. Expoe apenas NOMES de variaveis e se estao preenchidas.
"""
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)

TESTNET = "testnet"
REAL = "real"

# Nomes preferidos (separados por conta). Fallback: BYBIT_API_KEY/SECRET conforme BYBIT_TESTNET.
VAR_NAMES = {
    TESTNET: ("BYBIT_TESTNET_API_KEY", "BYBIT_TESTNET_API_SECRET"),
    REAL: ("BYBIT_REAL_API_KEY", "BYBIT_REAL_API_SECRET"),
}
LEGACY = ("BYBIT_API_KEY", "BYBIT_API_SECRET")


def _legacy_mode() -> str:
    return TESTNET if os.getenv("BYBIT_TESTNET", "true").strip().lower() == "true" else REAL


def resolve(mode: str) -> Tuple[Optional[str], Optional[str], str]:
    """Retorna (key, secret, origem) — origem e so o NOME das variaveis usadas."""
    k_name, s_name = VAR_NAMES[mode]
    k, s = os.getenv(k_name, "").strip(), os.getenv(s_name, "").strip()
    if k and s:
        return k, s, f"{k_name}/{s_name}"
    if _legacy_mode() == mode:
        k, s = os.getenv(LEGACY[0], "").strip(), os.getenv(LEGACY[1], "").strip()
        if k and s:
            return k, s, f"{LEGACY[0]}/{LEGACY[1]} (BYBIT_TESTNET={'true' if mode == TESTNET else 'false'})"
    return None, None, ""


def has_keys(mode: str) -> bool:
    k, s, _ = resolve(mode)
    return bool(k and s)


def env_names_status() -> Dict[str, str]:
    names = [*VAR_NAMES[TESTNET], *VAR_NAMES[REAL], *LEGACY, "BYBIT_TESTNET", "LLM_BASE_URL", "LLM_API_KEY",
             "LLM_MODEL", "DEFAULT_SYMBOL", "DEFAULT_INTERVAL", "DEFAULT_PAYOUT", "EXPIRY_CANDLES"]
    return {n: ("preenchida" if os.getenv(n, "").strip() else "ausente") for n in names}


def secret_values() -> List[str]:
    vals = []
    for n in [*VAR_NAMES[TESTNET], *VAR_NAMES[REAL], *LEGACY, "LLM_API_KEY"]:
        v = os.getenv(n, "").strip()
        if len(v) >= 6:
            vals.append(v)
    return vals


def scrub(text) -> str:
    """Remove qualquer valor de chave/segredo de um texto antes de exibir ou logar."""
    t = str(text)
    for v in secret_values():
        t = t.replace(v, "***")
    return t
