import hashlib
import hmac

import pytest
import requests

from brokers.bybit_client import BybitAPIError, BybitRest, BybitTransportError, compact_json, sign
from core.ratelimit import TokenBucket
from fakes import FakeBybit, FakeResp, err, ok


def mk(fake, **kw):
    sleeps = []
    r = BybitRest("https://api-testnet.bybit.com", "KEY", "SECRET", session=fake, sleep=sleeps.append,
                  limiter=TokenBucket(1000, 1000), time_fn=lambda: 1700000000.0, rng=lambda: 0.5, **kw)
    r.sleeps = sleeps
    return r


def test_signature_matches_spec():
    expected = hmac.new(b"SECRET", b"1700000000000KEY5000symbol=BTCUSDT", hashlib.sha256).hexdigest()
    assert sign("SECRET", "1700000000000", "KEY", "5000", "symbol=BTCUSDT") == expected


def test_signed_get_and_post_headers():
    f = FakeBybit()
    r = mk(f)
    r.request("GET", "/v5/position/list", {"category": "linear", "settleCoin": "USDT"})
    c = f.calls[-1]
    qs = "category=linear&settleCoin=USDT"
    assert c["headers"]["X-BAPI-SIGN"] == sign("SECRET", "1700000000000", "KEY", "5000", qs)
    assert c["headers"]["X-BAPI-API-KEY"] == "KEY" and c["headers"]["X-BAPI-RECV-WINDOW"] == "5000"
    body = {"category": "spot", "symbol": "BTCUSDT", "orderLinkId": "l1"}
    r.request("POST", "/v5/order/create", body=body, idempotent=True)
    c = f.calls[-1]
    assert c["headers"]["X-BAPI-SIGN"] == sign("SECRET", "1700000000000", "KEY", "5000", compact_json(body))


def test_missing_credentials_refused():
    r = BybitRest("https://x", "", "", session=FakeBybit())
    with pytest.raises(RuntimeError, match="Credenciais"):
        r.request("GET", "/v5/account/wallet-balance")


def test_get_retries_on_network_and_5xx():
    f = FakeBybit()
    f.script[("GET", "/v5/market/tickers")] = [requests.ConnectionError("x"), FakeResp({}, status=502), ok({"list": []})]
    r = mk(f)
    assert r.request("GET", "/v5/market/tickers", {"category": "spot"}, auth=False)["retCode"] == 0
    assert len(r.sleeps) == 2 and r.sleeps[1] > r.sleeps[0]       # backoff cresce


def test_get_gives_up_after_max_retries():
    f = FakeBybit()
    f.script[("GET", "/v5/market/tickers")] = [requests.Timeout("t")] * 10
    r = mk(f, max_retries=2)
    with pytest.raises(BybitTransportError):
        r.request("GET", "/v5/market/tickers", {}, auth=False)
    assert len(f.calls) == 3


def test_post_not_retried_unless_idempotent():
    f = FakeBybit()
    f.script[("POST", "/v5/order/create")] = [requests.Timeout("t"), requests.Timeout("t")]
    r = mk(f)
    with pytest.raises(BybitTransportError):
        r.request("POST", "/v5/order/create", body={"a": 1})
    assert len(f.calls) == 1
    f.calls.clear()
    f.script[("POST", "/v5/order/create")] = [requests.Timeout("t"), ok({"orderId": "1"})]
    assert r.request("POST", "/v5/order/create", body={"a": 1}, idempotent=True)["result"]["orderId"] == "1"
    assert len(f.calls) == 2


def test_rate_limit_retcode_retries_and_non_transient_raises():
    f = FakeBybit()
    f.script[("GET", "/v5/position/list")] = [err(10006, "Too many visits"), ok({"list": []})]
    r = mk(f)
    assert r.request("GET", "/v5/position/list", {})["retCode"] == 0
    f.script[("GET", "/v5/position/list")] = [err(10001, "bad param")]
    with pytest.raises(BybitAPIError) as e:
        r.request("GET", "/v5/position/list", {})
    assert e.value.ret_code == 10001


def test_timestamp_error_resyncs_clock():
    f = FakeBybit()
    f.script[("GET", "/v5/position/list")] = [err(10002, "timestamp"), ok({"list": []})]
    r = mk(f)
    r.request("GET", "/v5/position/list", {})
    assert r.time_offset_ms == 0 or isinstance(r.time_offset_ms, int)
    assert any(c["path"] == "/v5/market/time" for c in f.calls)
    assert f.calls[-1]["headers"]["X-BAPI-TIMESTAMP"] == str(1700000000000 + r.time_offset_ms)


def test_limit_status_header_penalizes():
    f = FakeBybit()
    f.script[("GET", "/v5/market/tickers")] = [FakeResp(ok({"list": []}), headers={"X-Bapi-Limit-Status": "0", "X-Bapi-Limit-Reset-Timestamp": "1700000002000"})]
    r = mk(f)
    called = []
    r.limiter.penalize = lambda s: called.append(s)
    r.request("GET", "/v5/market/tickers", {}, auth=False)
    assert called and 1.9 < called[0] <= 2.0
