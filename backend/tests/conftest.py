import os
import tempfile

# diretório de estado isolado ANTES de importar o app (o gerenciador de risco cria arquivos nele)
os.environ["GOLTRADER_STATE_DIR"] = tempfile.mkdtemp(prefix="gt-state-")

import pytest  # noqa: E402

from execution import state  # noqa: E402
from risk.risk_manager import InstitutionalRiskManager  # noqa: E402

ENV_TO_CLEAR = (
    "GOLTRADER_API_TOKEN", "GOLTRADER_ALLOW_LIVE", "ANALYZER_MODE", "GOLTRADER_DATA_SOURCE", "BYBIT_API_KEY",
    "BYBIT_API_SECRET", "GOLTRADER_MAX_ORDERS_PER_MIN", "GOLTRADER_MAX_DAILY_LOSS_USD", "GOLTRADER_ENABLE_IQ",
    "GOLTRADER_ALLOWED_VENUES", "GOLTRADER_PAPER_EQUITY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "IQ_EMAIL",
    "IQ_PASSWORD", "GOLTRADER_MAX_DAILY_DRAWDOWN_PCT",
)


@pytest.fixture(autouse=True)
def clean_env_and_state(monkeypatch, tmp_path):
    for k in ENV_TO_CLEAR:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GOLTRADER_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("GOLTRADER_PAPER_EQUITY", "10000")
    state.analyzer_mode = True
    state.autonomous_funnel = False
    state.kill_switch = False
    state.data_source = "SIMULADO"
    state.order_times.clear()
    state.daily_pnl_usd = 0.0
    state.active_positions.clear()
    state.execution_logs.clear()
    state.gate.ledger.clear()
    state.prices.clear()
    state.prices["BTCUSDT"] = 65000.0
    state.bybit = None
    state.iq = None
    state.risk = InstitutionalRiskManager(broker=state.risk.broker, state_dir=str(tmp_path),
                                          on_realized=state.risk.on_realized, on_breaker=state._on_breaker)
    yield
