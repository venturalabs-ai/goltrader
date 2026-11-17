import json

import pytest

from brokers import Mode, OrderRequest
from brokers.base import KillSwitchActive
from brokers.bybit import BybitAdapter
from brokers.bybit_client import BybitRest
from core.ratelimit import TokenBucket
from execution.engine import ExecutionEngine
from execution.risk import RiskGuard, RiskLimits, RiskRejected
from execution.state import StateStore
from fakes import FakeBybit, ok


def make(tmp_path, limits=None, fake=None, mode=Mode.TESTNET, creds=True, clock=None):
    fake = fake or FakeBybit()
    rest = BybitRest("https://api-testnet.bybit.com", "K" if creds else "", "S" if creds else "", session=fake,
                     sleep=lambda s: None, limiter=TokenBucket(1000, 1000), rng=lambda: 0.5)
    ads = {c: BybitAdapter(mode, category=c, rest=rest) for c in ("spot", "linear")}
    store = StateStore(tmp_path / "s" / "db.sqlite")
    eng = ExecutionEngine(ads, store, RiskGuard(limits or RiskLimits()), mode, tmp_path / "s",
                          clock=clock or (lambda: 1700000000.0), sleep=lambda s: None)
    return eng, fake, store


def test_submit_persists_before_and_after_send(tmp_path):
    eng, f, st = make(tmp_path)
    r = eng.submit(OrderRequest("BTCUSDT", "buy", 0.001, client_id="o1"), "linear")
    assert r.ok
    o = st.get_order("o1")
    assert o["status"] == "NEW" and o["order_id"] and o["symbol"] == "BTCUSDT" and o["side"] == "Buy"


def test_submit_is_idempotent_on_same_link(tmp_path):
    eng, f, st = make(tmp_path)
    eng.submit(OrderRequest("BTCUSDT", "buy", 0.001, client_id="dup"), "linear")
    r2 = eng.submit(OrderRequest("BTCUSDT", "buy", 0.001, client_id="dup"), "linear")
    assert r2.raw.get("idempotent") is True
    assert len([c for c in f.calls if c["path"] == "/v5/order/create"]) == 1


def test_risk_max_order_and_position(tmp_path):
    eng, f, st = make(tmp_path, RiskLimits(max_order_usd=100, max_position_usd=150))
    with pytest.raises(RiskRejected) as e:
        eng.submit(OrderRequest("BTCUSDT", "buy", 0.01), "linear")       # 600 USDT
    assert e.value.rule == "max_ordem"
    f.last_price = 50.0
    eng.submit(OrderRequest("BTCUSDT", "buy", 2, client_id="a"), "linear")   # 100 USDT
    st.upsert_position("linear", {"symbol": "BTCUSDT", "side": "Buy", "size": "2", "avgPrice": "50", "positionIdx": 0})
    with pytest.raises(RiskRejected) as e:
        eng.submit(OrderRequest("BTCUSDT", "buy", 2, client_id="b"), "linear")   # posição → 4*50=200 > 150
    assert e.value.rule == "max_posicao"
    # reduzir é sempre permitido
    eng.submit(OrderRequest("BTCUSDT", "sell", 2, client_id="c", reduce_only=True), "linear")


def test_risk_orders_per_minute(tmp_path):
    eng, f, st = make(tmp_path, RiskLimits(max_orders_per_min=2, max_order_usd=1e9, max_position_usd=1e9))
    f.last_price = 10.0
    for i in range(2):
        eng.submit(OrderRequest("ETHUSDT", "buy", 0.01, client_id=f"m{i}"), "linear")
    with pytest.raises(RiskRejected) as e:
        eng.submit(OrderRequest("ETHUSDT", "buy", 0.01, client_id="m3"), "linear")
    assert e.value.rule == "ordens_por_min"


def test_unknown_outcome_marked_and_reconcile_resolves(tmp_path):
    import requests
    eng, f, st = make(tmp_path)
    f.script[("POST", "/v5/order/create")] = [requests.Timeout("t")] * 6
    with pytest.raises(Exception):
        eng.submit(OrderRequest("BTCUSDT", "buy", 0.001, client_id="u1"), "linear")
    assert st.get_order("u1")["status"] == "UNKNOWN"
    # a corretora de fato aceitou
    f.orders["u1"] = {"category": "linear", "orderLinkId": "u1", "orderId": "oid-9", "orderStatus": "New", "symbol": "BTCUSDT",
                      "side": "Buy", "orderType": "Market", "qty": "0.001", "cumExecQty": "0", "avgPrice": "0"}
    summary = eng.reconcile()
    assert st.get_order("u1")["status"] == "New" and st.get_order("u1")["order_id"] == "oid-9"
    assert summary["lost"] == 0


def test_reconcile_marks_lost_and_syncs_positions_equity(tmp_path):
    eng, f, st = make(tmp_path)
    st.insert_order("ghost", "linear", "BTCUSDT", "Buy", "market", 0.001, None, False, "PENDING")
    f.positions = [{"symbol": "BTCUSDT", "side": "Buy", "size": "0.5", "avgPrice": "60000", "positionIdx": 0, "unrealisedPnl": "5"}]
    f.executions = [{"execId": "e1", "orderLinkId": "x", "symbol": "BTCUSDT", "side": "Buy", "execQty": "0.1", "execPrice": "60000",
                     "execFee": "0.5", "execPnl": "0", "execTime": "1700000000000"}]
    s = eng.reconcile()
    assert st.get_order("ghost")["status"] == "LOST" and s["lost"] == 1
    assert [p["symbol"] for p in st.positions("linear")] == ["BTCUSDT"] and s["positions"] == 1
    assert s["executions"] == 1 and s["equity"] == 10000.0
    assert eng.reconcile()["executions"] == 0           # dedupe por execId
    assert st.kv_get("last_reconcile_ms")


def test_reconcile_without_credentials_is_skipped(tmp_path):
    eng, f, st = make(tmp_path, creds=False)
    assert eng.reconcile() == {"skipped": "sem_credenciais"} and f.calls == []


def test_state_survives_restart(tmp_path):
    eng, f, st = make(tmp_path)
    eng.submit(OrderRequest("BTCUSDT", "buy", 0.001, client_id="persist"), "linear")
    st.kv_set("k", "v")
    st.close()
    st2 = StateStore(tmp_path / "s" / "db.sqlite")
    assert st2.get_order("persist")["status"] == "NEW" and st2.kv_get("k") == "v"


def test_kill_switch_blocks_orders_and_closes_all_reduce_only(tmp_path):
    eng, f, st = make(tmp_path)
    f.positions = [{"symbol": "BTCUSDT", "side": "Buy", "size": "0.02", "positionIdx": 0}]

    def after_close(*a, **k):
        pass
    rep = eng.kill_switch("teste")
    assert eng.kill_flag.exists() and eng.kill_active()
    closes = [c["body"] for c in f.calls if c["path"] == "/v5/order/create"]
    assert closes and all(c["reduceOnly"] for c in closes)
    assert st.kv_get("kill_executed_ms")
    with pytest.raises(KillSwitchActive):
        eng.submit(OrderRequest("BTCUSDT", "buy", 0.001), "linear")
    # reduce-only ainda é bloqueado pelo kill (só o close_all interno passa)
    eng.clear_kill()
    assert not eng.kill_active() and st.kv_get("kill_executed_ms") is None


def test_kill_pending_detects_external_flag(tmp_path):
    eng, f, st = make(tmp_path)
    assert not eng.kill_pending()
    eng.kill_flag.parent.mkdir(parents=True, exist_ok=True)
    eng.kill_flag.write_text("externo")
    assert eng.kill_pending()
    eng.kill_switch("flag externo", create_flag=False)
    assert not eng.kill_pending()


def test_daily_loss_halts_and_kills(tmp_path):
    eng, f, st = make(tmp_path, RiskLimits(max_daily_loss_usd=100))
    f.positions = [{"symbol": "BTCUSDT", "side": "Buy", "size": "0.02", "positionIdx": 0}]
    assert eng.update_equity(10000) is False                  # equity inicial do dia
    assert eng.update_equity(9950) is False and eng.daily_loss() == 50
    assert eng.update_equity(9890) is True                    # perda 110 ≥ 100
    assert "perda diária" in eng.halted()
    assert eng.kill_flag.exists()
    assert any(c["body"]["reduceOnly"] for c in f.calls if c["path"] == "/v5/order/create")
    eng.clear_kill()
    with pytest.raises(RiskRejected) as e:
        eng.submit(OrderRequest("BTCUSDT", "buy", 0.001), "linear")
    assert e.value.rule in ("halt",)


def test_daily_loss_resets_next_day(tmp_path):
    t = [1700000000.0]
    eng, f, st = make(tmp_path, RiskLimits(max_daily_loss_usd=100, kill_on_daily_loss=False), clock=lambda: t[0])
    eng.update_equity(10000)
    eng.update_equity(9800)
    assert eng.halted()
    t[0] += 86400
    assert eng.halted() is None
    eng.update_equity(9800)       # novo dia: equity inicial = 9800
    assert eng.daily_loss() == 0


def test_ws_events_update_state(tmp_path):
    eng, f, st = make(tmp_path)
    eng.handle_ws("order", [{"category": "linear", "orderLinkId": "w1", "orderId": "9", "symbol": "BTCUSDT", "side": "Buy",
                             "orderType": "Limit", "qty": "1", "price": "100", "orderStatus": "New"}])
    eng.handle_ws("order", [{"category": "linear", "orderLinkId": "w1", "orderId": "9", "orderStatus": "Filled", "cumExecQty": "1", "avgPrice": "100"}])
    assert st.get_order("w1")["status"] == "Filled"
    eng.handle_ws("order", [{"category": "linear", "orderId": "77", "symbol": "ETHUSDT", "side": "Sell", "orderStatus": "New"}])
    assert st.get_order("ext-77")["external"] == 1
    eng.handle_ws("execution", [{"category": "linear", "execId": "x1", "orderLinkId": "w1", "symbol": "BTCUSDT", "side": "Buy",
                                 "execQty": "1", "execPrice": "100", "execFee": "0.1", "execTime": "1700000000000"}])
    eng.handle_ws("execution", [{"category": "linear", "execId": "x1", "symbol": "BTCUSDT"}])
    assert st.db.execute("select count(*) from executions").fetchone()[0] == 1
    eng.handle_ws("position", [{"category": "linear", "symbol": "BTCUSDT", "side": "Buy", "size": "1", "positionIdx": 0, "entryPrice": "100"}])
    assert st.signed_position_qty("BTCUSDT") == 1
    eng.handle_ws("position", [{"category": "linear", "symbol": "BTCUSDT", "side": "", "size": "0", "positionIdx": 0}])
    assert st.signed_position_qty("BTCUSDT") == 0
    eng.handle_ws("wallet", [{"totalEquity": "9999", "coin": [{"coin": "USDT", "equity": "9999", "availableToWithdraw": "9000"}]}])
    assert st.wallet_coin("USDT") == 9999 and float(eng.status()["equity"]) == 9999


def test_spot_position_uses_wallet_balance(tmp_path):
    eng, f, st = make(tmp_path, RiskLimits(max_position_usd=100, max_order_usd=1000))
    f.last_price = 100.0
    st.set_wallet([{"coin": "BTC", "equity": "1", "availableToWithdraw": "1"}])
    with pytest.raises(RiskRejected) as e:
        eng.submit(OrderRequest("BTCUSDT", "buy", 0.5), "spot")        # já tem 1 BTC=100 USDT
    assert e.value.rule == "max_posicao"
