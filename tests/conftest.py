"""Configuração dos testes: SEM rede, estado isolado por teste."""
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def blocked(self, *a, **k):
        raise RuntimeError("REDE BLOQUEADA nos testes (use fakes)")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    for k in ("BYBIT_TESTNET", "GOLTRADER_ALLOW_LIVE", "ANALYZER_MODE", "GOLTRADER_LIVE_CONFIRMATION", "GT_BROKERS_ENABLED",
              "BYBIT_API_KEY", "BYBIT_API_SECRET", "BYBIT_TESTNET_API_KEY", "BYBIT_TESTNET_API_SECRET",
              "BYBIT_REAL_API_KEY", "BYBIT_REAL_API_SECRET", "GOLTRADER_ENABLE_UNOFFICIAL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GOLTRADER_STATE_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("GT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.chdir(tmp_path)
