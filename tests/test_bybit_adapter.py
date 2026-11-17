import pytest
import requests

from brokers import Mode, OrderRequest
from brokers.bybit import BybitAdapter, OptionTradingDisabled, OrderRejected
from brokers.bybit_client import BybitRest
from core.ratelimit import TokenBucket
from fakes import FakeBybit, err, ok


def mk(category="linear", fake=None):
    fake = fake or FakeBybit()
    rest = BybitRest("https://api-testnet.bybit.com", "K", "S", session=fake, sleep=lambda s: None,
                     limiter=TokenBucket(1000, 1000), rng=lambda: 0.5)
    return BybitAdapter(Mode.TESTNET, category=category, rest=rest), fake


def test_qty_and_price_normalization():
    a, _ = mk()
    assert a.normalize_qty("BTCUSDT", 0.0129999) == "0.012"
    assert a.normalize_price("BTCUSDT", 60000.74) == "60000.5"
    with pytest.raises(OrderRejected):
        a.normalize_qty("BTCUSDT", 0.0004)
    with pytest.raises(OrderRejected):
        a.normalize_qty("BTCUSDT", 1000)


def test_linear_market_order_body_and_idempotent_link():
    a, f = mk("linear")
    r = a.place_order(OrderRequest("BTCUSDT", "buy", 0.01, client_id="my-link-1"))
    assert r.ok and r.order_id
    body = f.calls[-1]["body"]
    assert body["orderLinkId"] == "my-link-1" and body["category"] == "linear" and body["side"] == "Buy"
    assert body["orderType"] == "Market" and body["timeInForce"] == "IOC" and body["qty"] == "0.01"
    assert "marketUnit" not in body and "reduceOnly" not in body


def test_generated_link_id_is_valid_and_unique():
    a, f = mk("linear")
    a.place_order(OrderRequest("BTCUSDT", "buy", 0.01))
    a.place_order(OrderRequest("BTCUSDT", "buy", 0.01))
    links = [c["body"]["orderLinkId"] for c in f.calls if c["path"] == "/v5/order/create"]
    assert len(set(links)) == 2 and all(len(l) <= 36 for l in links)


def test_spot_market_uses_base_coin_unit():
    a, f = mk("spot")
    a.place_order(OrderRequest("BTCUSDT", "sell", 0.01))
    assert f.calls[-1]["body"]["marketUnit"] == "baseCoin"


def test_limit_order_needs_price_and_gtc():
    a, f = mk("linear")
    with pytest.raises(ValueError):
        a.place_order(OrderRequest("BTCUSDT", "buy", 0.01, "limit"))
    a.place_order(OrderRequest("BTCUSDT", "buy", 0.01, "limit", price=59000.3, reduce_only=True))
    b = f.calls[-1]["body"]
    assert b["timeInForce"] == "GTC" and b["price"] == "59000" or b["price"] == "59000.0" or b["price"].startswith("59000")
    assert b["reduceOnly"] is True


def test_reduce_only_rejected_on_spot():
    a, _ = mk("spot")
    with pytest.raises(ValueError):
        a.place_order(OrderRequest("BTCUSDT", "sell", 0.01, reduce_only=True))


def test_timeout_then_retry_does_not_duplicate():
    """POST cai por timeout depois de a corretora aceitar: o retry usa o MESMO orderLinkId → 110072 → devolve a existente."""
    a, f = mk("linear")
    link = "retry-link"
    f.orders[link] = {"category": "linear", "orderLinkId": link, "orderId": "oid-x", "orderStatus": "New", "symbol": "BTCUSDT"}
    r = a.place_order(OrderRequest("BTCUSDT", "buy", 0.01, client_id=link))
    assert r.ok and r.order_id == "oid-x" and r.raw["duplicate"] is True
    assert len(f.orders) == 1


def test_post_network_failure_retries_with_same_link():
    a, f = mk("linear")
    f.script[("POST", "/v5/order/create")] = [requests.Timeout("t")]
    r = a.place_order(OrderRequest("BTCUSDT", "buy", 0.01, client_id="same-link"))
    creates = [c for c in f.calls if c["path"] == "/v5/order/create"]
    assert r.ok and len(creates) == 2 and creates[0]["body"]["orderLinkId"] == creates[1]["body"]["orderLinkId"] == "same-link"


def test_api_error_returns_not_ok():
    a, f = mk("linear")
    f.script[("POST", "/v5/order/create")] = [err(110007, "insufficient balance")]
    r = a.place_order(OrderRequest("BTCUSDT", "buy", 0.01))
    assert not r.ok and "110007" in r.status


def test_option_trading_disabled_but_public_data_ok():
    a, f = mk("option")
    with pytest.raises(OptionTradingDisabled):
        a.place_order(OrderRequest("BTC-30DEC26-100000-C", "buy", 0.1))
    f.script[("GET", "/v5/market/tickers")] = [ok({"list": [{"symbol": "BTC-30DEC26-100000-C", "markIv": "0.6"}]})]
    assert a.option_tickers("BTC")[0]["markIv"] == "0.6"
    assert f.calls[-1]["query"]["category"] == "option"
    assert a.cancel_all() == {}


def test_paper_mode_sends_nothing():
    f = FakeBybit()
    rest = BybitRest("https://x", "K", "S", session=f)
    a = BybitAdapter(Mode.PAPER, category="linear", rest=rest)
    r = a.place_order(OrderRequest("BTCUSDT", "buy", 0.01, client_id="p1"))
    assert r.status == "FILLED_SIMULATED" and f.calls == []


def test_positions_filter_and_close_all_reduce_only():
    a, f = mk("linear")
    f.positions = [{"symbol": "BTCUSDT", "side": "Buy", "size": "0.02", "positionIdx": 0},
                   {"symbol": "ETHUSDT", "side": "Sell", "size": "0.5", "positionIdx": 0},
                   {"symbol": "XRPUSDT", "side": "Buy", "size": "0", "positionIdx": 0}]
    assert len(a.positions()) == 2
    rep = a.close_all()
    orders = [c["body"] for c in f.calls if c["path"] == "/v5/order/create"]
    assert f.cancelled_all == 1 and len(rep["closed"]) == 2
    by = {o["symbol"]: o for o in orders}
    assert by["BTCUSDT"]["side"] == "Sell" and by["ETHUSDT"]["side"] == "Buy"
    assert all(o["reduceOnly"] is True and o["orderType"] == "Market" and o["orderLinkId"].startswith("gtkill") for o in orders)


def test_close_all_works_even_with_kill_flag_active():
    from brokers.base import kill_flag_paths
    a, f = mk("linear")
    f.positions = [{"symbol": "BTCUSDT", "side": "Buy", "size": "0.02", "positionIdx": 0}]
    p = kill_flag_paths()[0]; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("x")
    assert len(a.close_all()["closed"]) == 1
    from brokers.base import KillSwitchActive
    with pytest.raises(KillSwitchActive):
        a.place_order(OrderRequest("BTCUSDT", "buy", 0.01))


def test_credentials_picked_by_mode(monkeypatch):
    from brokers.bybit import credentials
    env = {"BYBIT_TESTNET_API_KEY": "tk", "BYBIT_TESTNET_API_SECRET": "ts", "BYBIT_REAL_API_KEY": "rk", "BYBIT_REAL_API_SECRET": "rs"}
    assert credentials(Mode.TESTNET, env) == ("tk", "ts")
    assert credentials(Mode.LIVE, env) == ("rk", "rs")
    assert credentials(Mode.TESTNET, {"BYBIT_API_KEY": "k", "BYBIT_API_SECRET": "s"}) == ("k", "s")
