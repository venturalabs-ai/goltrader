from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BrokerAdapter, OrderRequest, OrderResult, Readiness


class StubBroker(BrokerAdapter):
    """Esqueleto: nada é enviado; qualquer método de negócio levanta NotImplementedError."""

    readiness = Readiness.STUB

    def _todo(self):
        raise NotImplementedError(f"Adapter '{self.name}' é apenas esqueleto. Ver docs/PLANO-HIBRIDO.md (tarefas de corretoras).")

    def klines(self, symbol: str, interval: str = "1", limit: int = 500): self._todo()
    def ticker(self, symbol: str) -> Dict[str, Any]: self._todo()
    def balance(self) -> Dict[str, Any]: self._todo()
    def positions(self) -> List[Dict[str, Any]]: self._todo()
    def _place_order(self, req: OrderRequest) -> OrderResult: self._todo()
    def cancel_all(self, symbol: Optional[str] = None) -> Dict[str, Any]: self._todo()
