import hashlib
import hmac

from connectors.bybit_async import BybitV5AsyncClient


def test_sign_headers_shape_and_hmac(monkeypatch):
    c = BybitV5AsyncClient(api_key="KEY", api_secret="SECRET", testnet=True)
    monkeypatch.setattr("time.time", lambda: 1700000000.123)
    body = '{"category":"linear","symbol":"BTCUSDT"}'
    h = c._sign_payload(body)
    assert set(h) == {"X-BAPI-API-KEY", "X-BAPI-SIGN", "X-BAPI-SIGN-TYPE", "X-BAPI-TIMESTAMP",
                      "X-BAPI-RECV-WINDOW", "Content-Type"}
    assert h["X-BAPI-API-KEY"] == "KEY" and h["X-BAPI-SIGN-TYPE"] == "2"
    assert h["X-BAPI-TIMESTAMP"] == "1700000000123" and h["X-BAPI-RECV-WINDOW"] == "5000"
    expected = hmac.new(b"SECRET", f"1700000000123KEY5000{body}".encode(), hashlib.sha256).hexdigest()
    assert h["X-BAPI-SIGN"] == expected and len(expected) == 64


def test_testnet_default_and_urls(monkeypatch):
    monkeypatch.delenv("BYBIT_TESTNET", raising=False)
    c = BybitV5AsyncClient()
    assert c.testnet is True and c.base_url == "https://api-testnet.bybit.com"
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    assert BybitV5AsyncClient().base_url == "https://api.bybit.com"


async def test_post_without_credentials_does_not_hit_network():
    c = BybitV5AsyncClient(api_key="", api_secret="", testnet=True)
    r = await c._post("/v5/order/create", {})
    assert r["retCode"] == -1 and "credenciais" in r["retMsg"]
    assert c._session is None
