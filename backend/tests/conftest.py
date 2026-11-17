import pytest

from execution import state


@pytest.fixture(autouse=True)
def clean_env_and_state(monkeypatch):
    for k in ("GOLTRADER_API_TOKEN", "GOLTRADER_ALLOW_LIVE", "ANALYZER_MODE", "GOLTRADER_DATA_SOURCE",
              "BYBIT_API_KEY", "BYBIT_API_SECRET", "GOLTRADER_MAX_ORDERS_PER_MIN", "GOLTRADER_MAX_DAILY_LOSS_USD"):
        monkeypatch.delenv(k, raising=False)
    state.analyzer_mode = True
    state.autonomous_funnel = False
    state.kill_switch = False
    state.data_source = "SIMULADO"
    state.order_times.clear()
    state.daily_pnl_usd = 0.0
    state.active_positions.clear()
    state.execution_logs.clear()
    state.bybit = None
    state.iq = None
    yield
