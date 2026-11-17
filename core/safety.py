"""Travas de segurança centrais (modo Testnet/LIVE, diretório de estado).

Regra de ouro: **Testnet por padrão**. LIVE só quando TODAS as condições abaixo valem:
  1. BYBIT_TESTNET=false
  2. GOLTRADER_ALLOW_LIVE=true
  3. ANALYZER_MODE não está "true" (modo analisador = sandbox)
  4. confirmação explícita "CONFIRMAR" (argumento ou GOLTRADER_LIVE_CONFIRMATION)
Qualquer combinação incompleta que peça LIVE falha alto (LiveBlocked) em vez de cair em silêncio.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

TRUE = {"1", "true", "yes", "y", "sim", "on"}


class LiveBlocked(PermissionError):
    """Conta real pedida, mas as travas de ambiente não permitem."""


def env_bool(name: str, default: bool = False, environ: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if environ is None else environ
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in TRUE


def state_dir(environ: Optional[Mapping[str, str]] = None, create: bool = True) -> Path:
    env = os.environ if environ is None else environ
    p = Path(env.get("GOLTRADER_STATE_DIR") or "state")
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def live_requested(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True se o ambiente pede conta real (BYBIT_TESTNET=false)."""
    return not env_bool("BYBIT_TESTNET", True, environ)


def assert_live_allowed(environ: Optional[Mapping[str, str]] = None, require_testnet_false: bool = True) -> None:
    env = os.environ if environ is None else environ
    if not env_bool("GOLTRADER_ALLOW_LIVE", False, env):
        raise LiveBlocked("LIVE bloqueado: GOLTRADER_ALLOW_LIVE precisa ser 'true'.")
    if require_testnet_false and not live_requested(env):
        raise LiveBlocked("LIVE bloqueado: BYBIT_TESTNET precisa ser 'false' para operar conta real.")
    if env_bool("ANALYZER_MODE", False, env):
        raise LiveBlocked("LIVE bloqueado: ANALYZER_MODE=true (modo analisador/sandbox).")


def resolve_mode(environ: Optional[Mapping[str, str]] = None, confirmation: Optional[str] = None):
    """Decide o modo de operação a partir do ambiente. Testnet se nada for dito."""
    from brokers.base import Mode, require_live_confirmation  # import tardio (evita ciclo)

    env = os.environ if environ is None else environ
    if not live_requested(env):
        return Mode.TESTNET
    assert_live_allowed(env)
    require_live_confirmation(Mode.LIVE, confirmation if confirmation is not None else env.get("GOLTRADER_LIVE_CONFIRMATION"))
    return Mode.LIVE
