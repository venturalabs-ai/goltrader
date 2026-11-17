import pytest
from fastapi.testclient import TestClient

import main
from execution import execute_single_order, state
from models import SmartOrderRequest


def O(venue="BYBIT_PERP", symbol="BTCUSDT", **kw):
    return SmartOrderRequest(symbol=symbol, venue=venue, side=kw.pop("side", "BUY"), amount=kw.pop("amount", 0.01), **kw)


def test_iq_venues_disabled_by_default():
    d = state.gate.verify_order(O("IQ_BINARY", symbol="EURUSD"))
    assert not d.allowed and d.code == "VENUE" and "ENABLE_IQ" in d.message
    d = state.gate.verify_order(O("IQ_DIGITAL", symbol="EURUSD"))
    assert not d.allowed


def test_iq_allowed_only_with_flag(monkeypatch):
    monkeypatch.setenv("GOLTRADER_ENABLE_IQ", "true")
    assert state.gate.verify_order(O("IQ_BINARY", symbol="EURUSD")).allowed


def test_allowed_venues_env(monkeypatch):
    monkeypatch.setenv("GOLTRADER_ALLOWED_VENUES", "BYBIT_PERP")
    assert state.gate.verify_order(O("BYBIT_PERP")).allowed
    d = state.gate.verify_order(O("BYBIT_SPOT", symbol="BTCUSDT"))
    assert not d.allowed and d.code == "VENUE"


def test_kill_switch_breaker_and_daily_loss_reject():
    state.kill_switch = True
    assert state.gate.verify_order(O()).code == "KILL_SWITCH"
    state.kill_switch = False
    state.risk.breaker_tripped, state.risk.breaker_reason = True, "teste"
    assert state.gate.verify_order(O()).code == "BREAKER"
    state.risk.breaker_tripped = False
    state.daily_pnl_usd = -500
    assert state.gate.verify_order(O()).code == "DAILY_LOSS"


def test_rate_limit_per_minute(monkeypatch):
    monkeypatch.setenv("GOLTRADER_MAX_ORDERS_PER_MIN", "2")
    codes = [state.gate.verify_order(O()).code for _ in range(3)]
    assert codes == ["OK", "OK", "RATE_LIMIT"]


def test_rejected_orders_do_not_consume_rate_limit(monkeypatch):
    monkeypatch.setenv("GOLTRADER_MAX_ORDERS_PER_MIN", "1")
    state.kill_switch = True
    for _ in range(5):
        state.gate.verify_order(O())
    state.kill_switch = False
    assert state.gate.verify_order(O()).allowed


def test_perp_requires_reference_price_leverage_cap_and_option_price():
    state.prices.clear()
    assert state.gate.verify_order(O()).code == "NO_PRICE"
    state.prices["BTCUSDT"] = 65000
    assert state.gate.verify_order(O(leverage=50)).code == "LEVERAGE"
    assert state.gate.verify_order(O("BYBIT_OPT", symbol="BTC-27DEC26-100000-C-USDT")).code == "OPT_PRICE"
    assert state.gate.verify_order(O("BYBIT_OPT", symbol="BTC-27DEC26-100000-C-USDT", price=100)).allowed


def test_live_requires_allow_and_real_wallet_and_margin_cap(monkeypatch):
    state.analyzer_mode = False
    assert state.gate.verify_order(O()).code == "ALLOW_LIVE"
    monkeypatch.setenv("GOLTRADER_ALLOW_LIVE", "true")
    assert state.gate.verify_order(O()).code == "EQUITY"  # paper equity NÃO vale para LIVE
    state.risk.set_wallet_equity(1000.0, "BYBIT_TESTNET")
    d = state.gate.verify_order(O(amount=1.0, leverage=5))  # margem 13000 > 20% de 1000
    assert d.code == "MARGIN" and not d.allowed
    d = state.gate.verify_order(O(amount=0.01, leverage=5))
    assert d.allowed and d.paper is False


def test_funnel_on_simulated_data_forced_paper(monkeypatch):
    monkeypatch.setenv("GOLTRADER_ALLOW_LIVE", "true")
    state.analyzer_mode = False
    state.data_source = "SIMULADO"
    d = state.gate.verify_order(O(), origin="FUNIL")
    assert d.allowed and d.paper is True


def test_ledger_records_every_decision_without_secrets(monkeypatch):
    monkeypatch.setenv("BYBIT_API_SECRET", "SUPERSECRETVALUE123")
    state.gate.verify_order(O())
    state.kill_switch = True
    state.gate.verify_order(O("BYBIT_SPOT"))
    led = state.gate.recent()
    assert len(led) == 2 and led[0]["allowed"] is False and led[1]["allowed"] is True
    assert "SUPERSECRETVALUE123" not in str(led)


async def test_every_execution_path_goes_through_gate(monkeypatch):
    calls = []
    orig = state.gate.verify_order
    monkeypatch.setattr(state.gate, "verify_order", lambda o, origin="MANUAL": (calls.append(origin), orig(o, origin))[1])
    await execute_single_order(O(), origin="MANUAL")
    await execute_single_order(O(), origin="FUNIL")
    assert calls == ["MANUAL", "FUNIL"]


async def test_paper_fill_registers_position_with_default_stop():
    r = await execute_single_order(O(leverage=5))
    assert r["status"] == "FILLED_PAPER" and r["position_id"] in state.risk.positions
    pos = state.risk.positions[r["position_id"]]
    assert pos.leverage == 5 and pos.stop_loss == pytest.approx(65000 * 0.99, abs=0.01) and pos.paper


def test_basket_and_options_endpoints_hit_gate(monkeypatch):
    monkeypatch.setenv("GOLTRADER_API_TOKEN", "tok")
    c = TestClient(main.app)
    h = {"Authorization": "Bearer tok"}
    r = c.post("/api/v1/place_concurrent_basket", headers=h, json=[
        {"symbol": "BTC-27DEC26-100000-C-USDT", "venue": "BYBIT_OPT", "side": "BUY", "amount": 1},  # sem preço
        {"symbol": "EURUSD", "venue": "IQ_BINARY", "side": "BUY", "amount": 1},
    ]).json()["executions"]
    assert [e["trust_code"] for e in r] == ["OPT_PRICE", "VENUE"]
    assert all(e["status"] == "REJECTED" for e in r)


def test_reset_breaker_endpoint_auth_and_confirm(monkeypatch):
    c = TestClient(main.app)
    assert c.post("/api/v1/risk/reset_breaker", json={"confirm": "CONFIRMAR"}).status_code == 503
    monkeypatch.setenv("GOLTRADER_API_TOKEN", "tok")
    h = {"Authorization": "Bearer tok"}
    assert c.post("/api/v1/risk/reset_breaker", json={"confirm": "CONFIRMAR"}).status_code == 401
    state.risk.breaker_tripped = True
    assert c.post("/api/v1/risk/reset_breaker", headers=h).status_code == 403
    assert c.post("/api/v1/risk/reset_breaker", headers=h, json={"confirm": "x"}).status_code == 403
    assert state.risk.breaker_tripped
    r = c.post("/api/v1/risk/reset_breaker", headers=h, json={"confirm": "CONFIRMAR"})
    assert r.status_code == 200 and r.json()["breaker_tripped"] is False
    assert r.json()["autonomous_funnel"] is False  # reset não religa o funil
    state.risk.breaker_tripped = True
    assert c.post("/api/v1/toggle_autonomous", headers=h).status_code == 409
