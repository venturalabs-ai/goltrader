"""Interface comum (adapter) para corretoras/exchanges do Goltrader híbrido.

Regras de segurança (valem para TODOS os adapters):
- Testnet/paper por padrão. Conta real exige `allow_live=True` explícito E o texto
  de confirmação "CONFIRMAR" (ver `require_live_confirmation`).
- Kill switch global (arquivo logs/KILL_SWITCH) bloqueia qualquer envio de ordem.
- Adapter desligado por padrão: só carrega se estiver em GT_BROKERS_ENABLED.
- Nenhuma chave no código: credenciais só via variáveis de ambiente (.env).
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class Mode(str, Enum):
    PAPER = "paper"      # simulado localmente, nenhuma chamada de ordem
    TESTNET = "testnet"  # sandbox da corretora
    LIVE = "live"        # conta real (exige confirmação explícita)


class Readiness(str, Enum):
    READY = "ready"      # operacional e testado
    PARTIAL = "partial"  # dados/consulta ok, ordens não validadas
    STUB = "stub"        # apenas esqueleto: levanta NotImplementedError


class LiveNotConfirmed(PermissionError):
    pass


class KillSwitchActive(RuntimeError):
    pass


@dataclass
class OrderRequest:
    symbol: str
    side: str                      # "buy" | "sell"
    qty: float
    order_type: str = "market"     # "market" | "limit"
    price: Optional[float] = None
    reduce_only: bool = False
    client_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderResult:
    ok: bool
    order_id: Optional[str]
    status: str
    mode: Mode
    raw: Dict[str, Any] = field(default_factory=dict)


KILL_FLAG = Path(os.getenv("GT_LOG_DIR", "logs")) / "KILL_SWITCH"   # legado (fixado na importação)


def kill_flag_paths() -> List[Path]:
    """Arquivos-sentinela do kill switch (lidos a cada chamada): logs/KILL_SWITCH e <state>/KILL_SWITCH."""
    return [Path(os.getenv("GT_LOG_DIR", "logs")) / "KILL_SWITCH",
            Path(os.getenv("GOLTRADER_STATE_DIR") or "state") / "KILL_SWITCH"]


def kill_active() -> bool:
    return KILL_FLAG.exists() or any(p.exists() for p in kill_flag_paths())


def require_live_confirmation(mode: Mode, confirmation: Optional[str]) -> None:
    if mode is Mode.LIVE and (confirmation or "").strip() != "CONFIRMAR":
        raise LiveNotConfirmed('Conta real exige confirmação explícita: digite "CONFIRMAR".')


class BrokerAdapter(ABC):
    """Contrato mínimo. Cada corretora implementa o que suporta e declara `capabilities`."""

    name: str = "base"
    display_name: str = "Base"
    readiness: Readiness = Readiness.STUB
    asset_classes: tuple = ()      # ("crypto", "forex", "stocks", "options", ...)
    official_api: str = ""
    notes: str = ""
    unofficial: bool = False       # True = sem API oficial (risco de ToS/bloqueio)

    def __init__(self, mode: Mode = Mode.TESTNET, confirmation: Optional[str] = None):
        require_live_confirmation(mode, confirmation)
        if mode is Mode.LIVE:
            from core.safety import assert_live_allowed
            assert_live_allowed(require_testnet_false=False)
        self.mode = mode

    # --- dados de mercado ---
    @abstractmethod
    def klines(self, symbol: str, interval: str = "1", limit: int = 500):
        """Retorna DataFrame OHLCV indexado por timestamp UTC."""

    @abstractmethod
    def ticker(self, symbol: str) -> Dict[str, Any]: ...

    # --- conta ---
    @abstractmethod
    def balance(self) -> Dict[str, Any]: ...

    @abstractmethod
    def positions(self) -> List[Dict[str, Any]]: ...

    # --- ordens (sempre via _guard) ---
    @abstractmethod
    def _place_order(self, req: OrderRequest) -> OrderResult: ...

    def place_order(self, req: OrderRequest) -> OrderResult:
        if kill_active():
            raise KillSwitchActive("Kill switch ativo: ordens bloqueadas.")
        if self.mode is Mode.PAPER:
            return OrderResult(True, f"paper-{req.client_id or 'x'}", "FILLED_SIMULATED", self.mode, {"req": req.__dict__})
        return self._place_order(req)

    @abstractmethod
    def cancel_all(self, symbol: Optional[str] = None) -> Dict[str, Any]: ...

    def close_all(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Fecha tudo com ordens reduce-only (usado pelo kill switch). Adapters que operam devem implementar."""
        raise NotImplementedError(f"Adapter '{self.name}' não implementa close_all.")

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "display_name": self.display_name, "readiness": self.readiness.value,
                "asset_classes": list(self.asset_classes), "official_api": self.official_api,
                "mode": self.mode.value, "notes": self.notes, "unofficial": self.unofficial}
