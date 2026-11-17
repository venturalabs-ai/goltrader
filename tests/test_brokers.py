import pytest

from brokers import Mode, OrderRequest, catalog, get_broker
from brokers.base import KILL_FLAG, KillSwitchActive, LiveNotConfirmed


def test_catalog_has_all_and_only_bybit_enabled(monkeypatch):
    monkeypatch.delenv("GT_BROKERS_ENABLED", raising=False)
    c = {x["name"]: x for x in catalog()}
    assert set(c) == {"bybit", "binance", "okx", "coinbase", "kraken", "ibkr", "pepperstone", "iqoption"}
    assert [n for n, x in c.items() if x["enabled"]] == ["bybit"]


def test_disabled_adapter_refuses(monkeypatch):
    monkeypatch.delenv("GT_BROKERS_ENABLED", raising=False)
    with pytest.raises(PermissionError):
        get_broker("binance")


def test_live_requires_confirmation(monkeypatch):
    monkeypatch.delenv("GT_BROKERS_ENABLED", raising=False)
    with pytest.raises(LiveNotConfirmed):
        get_broker("bybit", Mode.LIVE)
    with pytest.raises(LiveNotConfirmed):
        get_broker("bybit", Mode.LIVE, confirmation="sim")


def test_stub_not_implemented(monkeypatch):
    monkeypatch.setenv("GT_BROKERS_ENABLED", "iqoption")
    b = get_broker("iqoption", Mode.TESTNET)
    with pytest.raises(NotImplementedError):
        b.balance()


def test_paper_order_and_kill_switch(monkeypatch, tmp_path):
    monkeypatch.delenv("GT_BROKERS_ENABLED", raising=False)
    b = get_broker("bybit", Mode.PAPER)
    r = b.place_order(OrderRequest("BTCUSDT", "buy", 0.001, client_id="t1"))
    assert r.ok and r.status == "FILLED_SIMULATED"
    KILL_FLAG.parent.mkdir(parents=True, exist_ok=True)
    KILL_FLAG.write_text("test")
    try:
        with pytest.raises(KillSwitchActive):
            b.place_order(OrderRequest("BTCUSDT", "buy", 0.001))
    finally:
        KILL_FLAG.unlink()
