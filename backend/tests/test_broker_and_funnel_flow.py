from connectors.bybit_async import BybitV5AsyncClient
from engine import Engine
from execution import state
from feeds import SimulatedFeed
from risk.brokers import BybitBroker


def make_client(responses):
    c = BybitV5AsyncClient(api_key="k", api_secret="s", testnet=True)
    sent = []

    async def post(path, payload):
        sent.append((path, payload))
        return responses.get(path, {"retCode": 0})

    async def get(path, params):
        return responses[path]

    c._post, c._get_private = post, get
    return c, sent


async def test_bybit_broker_close_is_reduce_only_market_opposite_side():
    c, sent = make_client({})
    b = BybitBroker(lambda: c)
    r = await b.close_position("BTCUSDT", "BUY", 0.123)
    assert r["ok"]
    path, p = sent[0]
    assert path == "/v5/order/create" and p["side"] == "Sell" and p["reduceOnly"] is True
    assert p["orderType"] == "Market" and p["qty"] == "0.123" and p["category"] == "linear"
    await b.close_position("BTCUSDT", "SELL", 1)
    assert sent[1][1]["side"] == "Buy"


async def test_bybit_broker_close_all_uses_exchange_positions():
    c, sent = make_client({"/v5/position/list": {"retCode": 0, "result": {"list": [
        {"symbol": "BTCUSDT", "side": "Buy", "size": "0.5"},
        {"symbol": "ETHUSDT", "side": "Sell", "size": "2"},
        {"symbol": "SOLUSDT", "side": "", "size": "0"},
    ]}}})
    r = await BybitBroker(lambda: c).close_all()
    assert r == {"ok": True, "closed": 2, "failed": []}
    assert [(p["symbol"], p["side"], p["reduceOnly"]) for _, p in sent] == [("BTCUSDT", "Sell", True), ("ETHUSDT", "Buy", True)]


async def test_equity_from_wallet_testnet_label():
    c, _ = make_client({"/v5/account/wallet-balance": {"retCode": 0, "result": {"list": [{"totalEquity": "4321.5"}]}}})
    assert await BybitBroker(lambda: c).get_equity() == (4321.5, "BYBIT_TESTNET")
    await state.risk.refresh_equity()  # broker padrão sem chaves -> permanece PAPER
    assert state.risk.equity_source == "PAPER"
    state.risk.broker = BybitBroker(lambda: c)
    await state.risk.refresh_equity()
    assert state.risk.equity() == 4321.5 and state.risk.equity_source == "BYBIT_TESTNET"


async def test_wallet_without_keys_returns_none():
    c = BybitV5AsyncClient(api_key="", api_secret="", testnet=True)
    assert await BybitBroker(lambda: c).get_equity() is None


async def test_autonomous_signal_goes_kelly_gate_register():
    eng = Engine(feed=SimulatedFeed(seed=1))
    state.autonomous_funnel = True
    sig = {"symbol": "BTCUSDT", "direction": "BUY", "price": 65000.0, "confidence": 90, "reasons": []}
    await eng._maybe_fire(sig)
    assert len(state.risk.positions) == 1
    pos = next(iter(state.risk.positions.values()))
    assert pos.paper is True and pos.stop_loss == 64350.0  # SL 1% do preço de referência
    assert state.gate.recent(1)[0]["origin"] == "FUNIL" and state.gate.recent(1)[0]["allowed"]
    # risco ≈ 0.1% de 10000 = 10 USD no stop
    assert abs(pos.qty * (pos.entry - pos.stop_loss) - 10) < 1.0


async def test_autonomous_without_equity_is_refused(monkeypatch):
    monkeypatch.delenv("GOLTRADER_PAPER_EQUITY")
    eng = Engine(feed=SimulatedFeed(seed=1))
    state.autonomous_funnel = True
    await eng._maybe_fire({"symbol": "BTCUSDT", "direction": "BUY", "price": 65000.0})
    assert not state.risk.positions and state.risk.events[0]["kind"] == "SIZING_REJECT"


async def test_tick_payload_has_risk_and_ledger():
    eng = Engine(feed=SimulatedFeed(seed=2))
    p = await eng.tick()
    assert p["risk_telemetry"]["win_rate_rolling"] == 0.5 and p["risk_telemetry"]["equity_source"] == "PAPER"
    assert "trust_ledger" in p
