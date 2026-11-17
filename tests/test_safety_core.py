import pytest

from brokers.base import LiveNotConfirmed, Mode
from core.lease import Lease
from core.ratelimit import SlidingWindow, TokenBucket
from core.retry import backoff_delays, retry_call
from core.safety import LiveBlocked, resolve_mode, state_dir


def test_default_is_testnet():
    assert resolve_mode({}) is Mode.TESTNET
    assert resolve_mode({"BYBIT_TESTNET": "true", "GOLTRADER_ALLOW_LIVE": "true"}) is Mode.TESTNET


def test_live_blocked_without_allow_flag():
    with pytest.raises(LiveBlocked):
        resolve_mode({"BYBIT_TESTNET": "false"}, confirmation="CONFIRMAR")


def test_live_blocked_by_analyzer_mode():
    with pytest.raises(LiveBlocked):
        resolve_mode({"BYBIT_TESTNET": "false", "GOLTRADER_ALLOW_LIVE": "true", "ANALYZER_MODE": "true"}, confirmation="CONFIRMAR")


def test_live_needs_confirmar():
    env = {"BYBIT_TESTNET": "false", "GOLTRADER_ALLOW_LIVE": "true"}
    with pytest.raises(LiveNotConfirmed):
        resolve_mode(env)
    with pytest.raises(LiveNotConfirmed):
        resolve_mode(env, confirmation="sim")
    assert resolve_mode(env, confirmation="CONFIRMAR") is Mode.LIVE
    assert resolve_mode({**env, "GOLTRADER_LIVE_CONFIRMATION": "CONFIRMAR"}) is Mode.LIVE


def test_adapter_live_requires_all_gates(monkeypatch):
    from brokers import get_broker
    with pytest.raises(LiveBlocked):   # confirmado mas sem ALLOW_LIVE
        get_broker("bybit", Mode.LIVE, confirmation="CONFIRMAR")
    monkeypatch.setenv("GOLTRADER_ALLOW_LIVE", "true")
    with pytest.raises(LiveBlocked):   # BYBIT_TESTNET ainda true
        get_broker("bybit", Mode.LIVE, confirmation="CONFIRMAR")
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    assert get_broker("bybit", Mode.LIVE, confirmation="CONFIRMAR").mode is Mode.LIVE


def test_state_dir_created(tmp_path):
    p = state_dir({"GOLTRADER_STATE_DIR": str(tmp_path / "x" / "y")})
    assert p.is_dir()


def test_token_bucket_waits():
    t = [0.0]
    slept = []
    tb = TokenBucket(2, 2, clock=lambda: t[0], sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
    tb.acquire(); tb.acquire()
    assert slept == []
    tb.acquire()
    assert slept and abs(slept[0] - 0.5) < 1e-6


def test_sliding_window():
    t = [0.0]
    w = SlidingWindow(2, 60, clock=lambda: t[0])
    w.record(); w.record()
    assert not w.allow()
    t[0] = 61
    assert w.allow()


def test_backoff_and_retry():
    d = list(backoff_delays(4, base=1, factor=2, jitter=0))
    assert d == [1, 2, 4, 8]
    calls = {"n": 0}
    sleeps = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("x")
        return "ok"
    assert retry_call(flaky, 5, lambda e: isinstance(e, ConnectionError), sleep=sleeps.append) == "ok"
    assert len(sleeps) == 2
    with pytest.raises(ValueError):
        retry_call(lambda: (_ for _ in ()).throw(ValueError()), 3, lambda e: False)


def test_lease_exclusive_and_expiry(tmp_path):
    t = [100.0]
    a = Lease(tmp_path / "l.db", owner="A", ttl=10, clock=lambda: t[0])
    b = Lease(tmp_path / "l.db", owner="B", ttl=10, clock=lambda: t[0])
    assert a.acquire()
    assert not b.acquire()
    assert b.holder() == "A"
    t[0] += 5
    assert a.renew()
    t[0] += 11                      # A expira
    assert b.acquire()
    assert not a.renew()            # A não reconquista sozinho
    b.release()
    assert a.acquire()
