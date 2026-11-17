from types import SimpleNamespace as NS

import pytest

from brokers import Mode, OrderRequest, catalog, get_broker
from brokers.base import KillSwitchActive, LiveNotConfirmed
from brokers.ccxt_base import SandboxUnavailable
from brokers.ibkr import IbkrAdapter, IbkrPortMismatch
from brokers.iqoption import UnofficialDisabled
from brokers.pepperstone import CTraderTransport, PepperstoneAdapter
from core.safety import LiveBlocked


class FakeCcxt:
    has = {"fetchPositions": True}

    def __init__(self):
        self.orders, self.cancelled = [], []

    def fetch_ohlcv(self, sym, tf, limit=100):
        return [[1700000060000, 2, 3, 1, 2.5, 10], [1700000000000, 1, 2, 0.5, 1.5, 5]]

    def fetch_ticker(self, sym):
        return {"symbol": sym, "last": 100}

    def fetch_balance(self):
        return {"USDT": {"free": 5}}

    def fetch_positions(self):
        return [{"symbol": "BTC/USDT:USDT", "contracts": 2, "side": "long"}, {"symbol": "ETH/USDT:USDT", "contracts": 0, "side": "long"}]

    def create_order(self, sym, typ, side, qty, price, params):
        self.orders.append((sym, typ, side, qty, price, params))
        return {"id": "x1", "status": "open"}

    def cancel_all_orders(self, sym=None):
        self.cancelled.append(sym)
        return []


@pytest.mark.parametrize("name", ["binance", "okx"])
def test_ccxt_adapters_roundtrip(monkeypatch, name):
    monkeypatch.setenv("GT_BROKERS_ENABLED", name)
    fx = FakeCcxt()
    b = get_broker(name, Mode.TESTNET, exchange=fx)
    df = b.klines("BTCUSDT", "1", 10)
    assert list(df.index) == sorted(df.index) and df["close"].iloc[-1] == 2.5
    assert b.ticker("BTCUSDT")["symbol"] == "BTC/USDT"
    r = b.place_order(OrderRequest("BTCUSDT", "buy", 0.1, client_id="cid1", reduce_only=True))
    assert r.ok and fx.orders[0][0] == "BTC/USDT" and fx.orders[0][5] == {"clientOrderId": "cid1", "reduceOnly": True}
    rep = b.close_all()
    assert fx.orders[-1][2] == "sell" and fx.orders[-1][5] == {"reduceOnly": True} and len(rep["closed"]) == 1
    assert len(b.positions()) == 1


def test_ccxt_kill_switch_and_live_gates(monkeypatch):
    monkeypatch.setenv("GT_BROKERS_ENABLED", "binance,kraken,coinbase")
    b = get_broker("binance", Mode.TESTNET, exchange=FakeCcxt())
    from brokers.base import kill_flag_paths
    p = kill_flag_paths()[0]; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("x")
    with pytest.raises(KillSwitchActive):
        b.place_order(OrderRequest("BTCUSDT", "buy", 1))
    p.unlink()
    with pytest.raises(LiveNotConfirmed):
        get_broker("binance", Mode.LIVE)
    with pytest.raises(LiveBlocked):
        get_broker("binance", Mode.LIVE, confirmation="CONFIRMAR")
    for n in ("kraken", "coinbase"):
        with pytest.raises(SandboxUnavailable):
            get_broker(n, Mode.TESTNET)
        assert get_broker(n, Mode.PAPER).place_order(OrderRequest("BTCUSDT", "buy", 1)).status == "FILLED_SIMULATED"


def test_ccxt_missing_library_gives_clear_error(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "ccxt", None)
    monkeypatch.setenv("GT_BROKERS_ENABLED", "okx")
    with pytest.raises(RuntimeError, match="ccxt"):
        get_broker("okx", Mode.TESTNET).ticker("BTCUSDT")


class FakeIB:
    def __init__(self):
        self.placed, self.cancelled = [], 0
        self.connected = False

    def isConnected(self): return self.connected
    def connect(self, host, port, clientId=0, readonly=False): self.connected = True; self.args = (host, port, clientId)
    def accountSummary(self): return [NS(tag="NetLiquidation", value="1000"), NS(tag="Other", value="1")]
    def positions(self): return [NS(contract=NS(symbol="AAPL"), position=10, avgCost=150.0), NS(contract=NS(symbol="MSFT"), position=0, avgCost=1)]
    def placeOrder(self, contract, order):
        self.placed.append((contract, order)); return NS(orderStatus=NS(status="Submitted"), order=NS(orderId=7))
    def reqGlobalCancel(self): self.cancelled += 1


LIB = NS(Stock=lambda s, e, c: ("STK", s, e, c), Forex=lambda p: ("FX", p),
         MarketOrder=lambda a, q: NS(action=a, qty=q, kind="MKT", orderRef=None),
         LimitOrder=lambda a, q, p: NS(action=a, qty=q, price=p, kind="LMT", orderRef=None))


def test_ibkr_paper_flow(monkeypatch):
    monkeypatch.setenv("GT_BROKERS_ENABLED", "ibkr")
    ib = FakeIB()
    b = get_broker("ibkr", Mode.TESTNET, ib=ib, lib=LIB)
    assert b.balance() == {"NetLiquidation": "1000"} and ib.args == ("127.0.0.1", 7497, 1)
    assert [p["symbol"] for p in b.positions()] == ["AAPL"]
    r = b.place_order(OrderRequest("AAPL", "buy", 5, client_id="ref1"))
    assert r.ok and ib.placed[0][1].orderRef == "ref1" and ib.placed[0][0] == ("STK", "AAPL", "SMART", "USD")
    b.place_order(OrderRequest("PETR4", "buy", 100, "limit", 30.0, extra={"exchange": "BOVESPA", "currency": "BRL"}))
    assert ib.placed[1][0] == ("STK", "PETR4", "BOVESPA", "BRL") and ib.placed[1][1].kind == "LMT"
    rep = b.close_all()
    assert ib.cancelled == 1 and ib.placed[-1][1].action == "SELL" and rep["closed"] == [{"symbol": "AAPL", "qty": 10}]


def test_ibkr_port_must_match_mode(monkeypatch):
    monkeypatch.setenv("IBKR_PORT", "7496")
    with pytest.raises(IbkrPortMismatch):
        IbkrAdapter(Mode.TESTNET, ib=FakeIB(), lib=LIB)
    monkeypatch.setenv("IBKR_PORT", "7497")
    with pytest.raises(LiveBlocked):
        IbkrAdapter(Mode.LIVE, confirmation="CONFIRMAR", ib=FakeIB(), lib=LIB)


class FakeCT(CTraderTransport):
    def __init__(self): self.orders, self.closed, self.cancelled = [], [], 0
    def bars(self, symbol, interval, limit): return [{"timestamp": 1700000000000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 3}]
    def quote(self, symbol): return {"bid": 1.1, "ask": 1.2}
    def account(self): return {"balance": 1000}
    def positions(self): return [{"symbol": "EURUSD", "position_id": 11, "volume": 1000}]
    def new_order(self, symbol, side, volume, order_type, price, label, reduce_only):
        self.orders.append((symbol, side, volume, label)); return {"ok": True, "order_id": 5, "status": "NEW"}
    def close_position(self, position_id, volume): self.closed.append((position_id, volume)); return {}
    def cancel_all(self, symbol): self.cancelled += 1; return {}


def test_pepperstone_with_mock_transport(monkeypatch):
    monkeypatch.setenv("GT_BROKERS_ENABLED", "pepperstone")
    t = FakeCT()
    b = get_broker("pepperstone", Mode.TESTNET, transport=t)
    assert b.host == "demo.ctraderapi.com" and b.klines("EURUSD").iloc[0]["close"] == 1.5
    assert b.place_order(OrderRequest("EURUSD", "buy", 1000, client_id="lab1")).order_id == "5" and t.orders[0][3] == "lab1"
    rep = b.close_all()
    assert t.closed == [(11, 1000.0)] and t.cancelled == 1 and rep["closed"][0]["position_id"] == 11
    assert b.readiness.value == "stub"


def test_iqoption_unofficial_gates(monkeypatch):
    monkeypatch.setenv("GT_BROKERS_ENABLED", "iqoption")
    with pytest.raises(UnofficialDisabled):
        get_broker("iqoption", Mode.TESTNET)
    with pytest.raises(UnofficialDisabled):                      # real recusado sempre
        get_broker("iqoption", Mode.LIVE, confirmation="CONFIRMAR")
    monkeypatch.setenv("GOLTRADER_ENABLE_UNOFFICIAL", "true")
    with pytest.raises(UnofficialDisabled):
        get_broker("iqoption", Mode.LIVE, confirmation="CONFIRMAR")

    class API:
        def __init__(self): self.bal = []; self.bought = []
        def change_balance(self, m): self.bal.append(m)
        def get_balance(self): return 10000.0
        def buy(self, amount, active, action, exp): self.bought.append((amount, active, action, exp)); return True, 123
    api = API()
    b = get_broker("iqoption", Mode.TESTNET, api=api)
    assert b.unofficial and b.balance() == {"practice_balance": 10000.0}
    r = b.place_order(OrderRequest("EURUSD-OTC", "sell", 5))
    assert r.ok and api.bought == [(5, "EURUSD-OTC", "put", 1)] and set(api.bal) == {"PRACTICE"}
    assert b.positions() == []


def test_catalog_flags_and_defaults(monkeypatch):
    monkeypatch.delenv("GT_BROKERS_ENABLED", raising=False)
    c = {x["name"]: x for x in catalog()}
    assert [n for n, x in c.items() if x["enabled"]] == ["bybit"]
    assert c["iqoption"]["unofficial"] is True and c["bybit"]["unofficial"] is False
    assert c["pepperstone"]["readiness"] == "stub"
