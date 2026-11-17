import pytest

import execution
from execution import execute_single_order, state
from models import SmartOrderRequest


def order(venue="BYBIT_PERP", **kw):
    return SmartOrderRequest(symbol=kw.pop("symbol", "BTCUSDT"), venue=venue, side=kw.pop("side", "BUY"),
                             amount=kw.pop("amount", 0.01), **kw)


async def test_paper_by_default():
    r = await execute_single_order(order())
    assert r["status"] == "FILLED_PAPER" and r["paper"] is True


async def test_funnel_on_simulated_data_never_live(monkeypatch):
    monkeypatch.setenv("GOLTRADER_ALLOW_LIVE", "true")
    state.analyzer_mode = False  # mesmo em LIVE...
    state.data_source = "SIMULADO"

    called = []

    async def boom(*a, **k):
        called.append(1)
        raise AssertionError("não deve enviar ordem real")

    monkeypatch.setattr(execution, "_live_bybit", boom)
    monkeypatch.setattr(execution, "_live_iq", boom)
    r = await execute_single_order(order(), origin="FUNIL")
    assert r["status"] == "FILLED_PAPER" and r["paper"] is True and not called


async def test_live_without_allow_env_rejected(monkeypatch):
    state.analyzer_mode = False
    r = await execute_single_order(order())
    assert r["status"] == "REJECTED" and "ALLOW_LIVE" in r["error"]


async def test_live_routes_bybit_testnet_default(monkeypatch):
    monkeypatch.setenv("GOLTRADER_ALLOW_LIVE", "true")
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")
    state.analyzer_mode = False
    state.risk.set_wallet_equity(10000.0, "BYBIT_TESTNET")
    client = state.get_bybit()
    assert client.testnet is True and "testnet" in client.base_url
    sent = {}

    async def fake_post(path, payload):
        sent[path] = payload
        return {"retCode": 0, "result": {"orderId": "abc"}, "latency_ms": 1.0}

    monkeypatch.setattr(client, "_post", fake_post)
    r = await execute_single_order(order(leverage=5, take_profit=70000, stop_loss=60000))
    assert r["status"] == "SUBMITTED" and r["mode"] == "LIVE_TESTNET" and r["order_id"] == "abc"
    assert sent["/v5/position/set-leverage"]["buyLeverage"] == "5"
    p = sent["/v5/order/create"]
    assert p["category"] == "linear" and p["side"] == "Buy" and p["orderType"] == "Market" and p["orderLinkId"].startswith("gt-")


async def test_live_leverage_cap_and_option_needs_price(monkeypatch):
    monkeypatch.setenv("GOLTRADER_ALLOW_LIVE", "true")
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")
    state.analyzer_mode = False
    state.risk.set_wallet_equity(10000.0, "BYBIT_TESTNET")
    r = await execute_single_order(order(leverage=50))
    assert r["status"] == "REJECTED" and "alavancagem" in r["error"]
    r = await execute_single_order(order("BYBIT_OPT", symbol="BTC-27DEC26-100000-C-USDT"))
    assert r["status"] == "REJECTED" and "preço limite" in r["error"]


async def test_live_iq_digital_not_implemented(monkeypatch):
    monkeypatch.setenv("GOLTRADER_ALLOW_LIVE", "true")
    monkeypatch.setenv("GOLTRADER_ENABLE_IQ", "true")
    state.analyzer_mode = False
    r = await execute_single_order(order("IQ_DIGITAL", symbol="EURUSD-OTC"))
    assert r["status"] == "REJECTED" and "IQ_DIGITAL" in r["error"]


async def test_live_without_credentials_rejected(monkeypatch):
    monkeypatch.setenv("GOLTRADER_ALLOW_LIVE", "true")
    state.analyzer_mode = False
    state.risk.set_wallet_equity(10000.0, "BYBIT_TESTNET")
    r = await execute_single_order(order())
    assert r["status"] == "REJECTED" and "credenciais" in r["error"]


def test_order_ids_unique_and_not_numpy():
    ids = {execution.new_order_id() for _ in range(500)}
    assert len(ids) == 500
