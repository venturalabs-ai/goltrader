import pytest
from fastapi.testclient import TestClient

import main
from execution import state

BASKET = [{"symbol": "BTCUSDT", "venue": "BYBIT_PERP", "side": "BUY", "amount": 0.01}]


@pytest.fixture
def client():
    return TestClient(main.app)  # sem lifespan: sem tarefas de fundo/rede


def H(tok="s3cret"):
    return {"Authorization": f"Bearer {tok}"}


def test_saude_open_and_no_secrets(client, monkeypatch):
    monkeypatch.setenv("GOLTRADER_API_TOKEN", "s3cret")
    r = client.get("/api/saude")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["token_configured"] is True
    assert "s3cret" not in r.text


def test_post_without_token_env_returns_503(client):
    r = client.post("/api/v1/place_concurrent_basket", json=BASKET)
    assert r.status_code == 503 and "GOLTRADER_API_TOKEN" in r.json()["detail"]
    assert client.post("/api/v1/kill_switch").status_code == 503
    assert client.post("/api/v1/toggle_autonomous").status_code == 503


def test_post_wrong_or_missing_token_401(client, monkeypatch):
    monkeypatch.setenv("GOLTRADER_API_TOKEN", "s3cret")
    assert client.post("/api/v1/place_concurrent_basket", json=BASKET).status_code == 401
    assert client.post("/api/v1/place_concurrent_basket", json=BASKET, headers=H("x")).status_code == 401


def test_basket_paper_fill_with_valid_token(client, monkeypatch):
    monkeypatch.setenv("GOLTRADER_API_TOKEN", "s3cret")
    r = client.post("/api/v1/place_concurrent_basket", json=BASKET, headers=H())
    assert r.status_code == 200
    ex = r.json()["executions"][0]
    assert ex["status"] == "FILLED_PAPER" and ex["paper"] is True and ex["mode"] == "SANDBOX_ANALYZER"
    assert ex["order_id"].startswith("GT-")


def test_basket_validation(client, monkeypatch):
    monkeypatch.setenv("GOLTRADER_API_TOKEN", "s3cret")
    bad = [{"symbol": "BTCUSDT", "venue": "FOO", "side": "BUY", "amount": 1}]
    assert client.post("/api/v1/place_concurrent_basket", json=bad, headers=H()).status_code == 422
    assert client.post("/api/v1/place_concurrent_basket", json=[], headers=H()).status_code == 422


def test_live_requires_allow_env_and_confirm(client, monkeypatch):
    monkeypatch.setenv("GOLTRADER_API_TOKEN", "s3cret")
    # sem GOLTRADER_ALLOW_LIVE
    r = client.post("/api/v1/toggle_mode", json={"confirm": "CONFIRMAR"}, headers=H())
    assert r.status_code == 403 and state.analyzer_mode is True
    monkeypatch.setenv("GOLTRADER_ALLOW_LIVE", "true")
    # sem body / confirmação errada
    assert client.post("/api/v1/toggle_mode", headers=H()).status_code == 403
    assert client.post("/api/v1/toggle_mode", json={"confirm": "confirmar"}, headers=H()).status_code == 403
    assert state.analyzer_mode is True
    # correto
    r = client.post("/api/v1/toggle_mode", json={"confirm": "CONFIRMAR"}, headers=H())
    assert r.status_code == 200 and r.json()["analyzer_mode"] is False
    # voltar ao analyzer não exige confirmação
    r = client.post("/api/v1/toggle_mode", headers=H())
    assert r.status_code == 200 and r.json()["analyzer_mode"] is True


def test_defaults_safe():
    assert state.analyzer_mode is True and state.autonomous_funnel is False


def test_kill_switch_blocks_orders_and_reverts_to_paper(client, monkeypatch):
    monkeypatch.setenv("GOLTRADER_API_TOKEN", "s3cret")
    monkeypatch.setenv("GOLTRADER_ALLOW_LIVE", "true")
    client.post("/api/v1/toggle_mode", json={"confirm": "CONFIRMAR"}, headers=H())
    state.autonomous_funnel = True
    r = client.post("/api/v1/kill_switch", headers=H())
    assert r.json() == {"kill_switch": True, "analyzer_mode": True, "autonomous_funnel": False, "cancel_all": None}
    ex = client.post("/api/v1/place_concurrent_basket", json=BASKET, headers=H()).json()["executions"][0]
    assert ex["status"] == "REJECTED" and "KILL_SWITCH" in ex["error"]
    assert client.post("/api/v1/toggle_autonomous", headers=H()).status_code == 409
    assert client.post("/api/v1/toggle_mode", json={"confirm": "CONFIRMAR"}, headers=H()).status_code == 409
    r = client.post("/api/v1/kill_switch", json={"active": False}, headers=H())
    assert r.json()["kill_switch"] is False and r.json()["autonomous_funnel"] is False


def test_rate_limit_and_daily_loss(client, monkeypatch):
    monkeypatch.setenv("GOLTRADER_API_TOKEN", "s3cret")
    monkeypatch.setenv("GOLTRADER_MAX_ORDERS_PER_MIN", "2")
    res = client.post("/api/v1/place_concurrent_basket", json=BASKET * 3, headers=H()).json()["executions"]
    assert [e["status"] for e in res].count("FILLED_PAPER") == 2
    assert any("ordens/min" in e.get("error", "") for e in res)
    state.order_times.clear()
    monkeypatch.setenv("GOLTRADER_MAX_ORDERS_PER_MIN", "30")
    state.register_pnl(-250)  # estoura perda diária (200) -> kill switch
    assert state.kill_switch is True


def test_cors_not_wildcard(client, monkeypatch):
    r = client.options("/api/saude", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") != "*"
    assert "access-control-allow-origin" not in r.headers
    r = client.options("/api/saude", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_ws_requires_token(monkeypatch):
    monkeypatch.setenv("GOLTRADER_API_TOKEN", "s3cret")
    c = TestClient(main.app)
    with pytest.raises(Exception):
        with c.websocket_connect("/ws/pulse"):
            pass
    with pytest.raises(Exception):
        with c.websocket_connect("/ws/pulse?token=wrong"):
            pass
    with c.websocket_connect("/ws/pulse?token=s3cret") as ws:
        data = ws.receive_json()
    assert data["data_source"] == "SIMULADO" and data["simulated"] is True
    assert data["analyzer_mode"] is True and data["autonomous_funnel"] is False
    for k in ("options_analytics", "market_news", "economic_calendar", "order_blocks", "derivatives_pulse"):
        assert k in data
    assert data["economic_calendar"]["source"] == "EXEMPLO"


def test_ws_without_token_env_rejects_non_loopback():
    c = TestClient(main.app)  # host 'testclient' != loopback
    with pytest.raises(Exception):
        with c.websocket_connect("/ws/pulse"):
            pass
