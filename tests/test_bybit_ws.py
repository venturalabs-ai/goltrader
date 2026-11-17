import asyncio
import json

import pytest

from brokers.bybit_ws import BybitPrivateWS, WSAuthError, ws_signature
from fakes import FakeWS


def msg(topic, data):
    return json.dumps({"topic": topic, "data": data})


def test_signature_value():
    import hashlib, hmac
    assert ws_signature("S", 123) == hmac.new(b"S", b"GET/realtime123", hashlib.sha256).hexdigest()


def run(coro):
    return asyncio.run(coro)


def test_auth_subscribe_dispatch_and_stop():
    ws = FakeWS([msg("order", [{"orderLinkId": "a"}]), msg("wallet", [{"totalEquity": "1"}]), json.dumps({"op": "pong"})])
    got = []
    holder = {}

    async def on_msg(topic, data):
        got.append((topic, data))
        if topic == "wallet":
            holder["c"].stop()

    async def connect(url):
        return ws

    async def go():
        c = BybitPrivateWS("wss://x", "KEY", "SEC", on_msg, connect=connect, time_fn=lambda: 1000.0, ping_interval=0.05)
        holder["c"] = c
        await asyncio.wait_for(c.run_forever(), 5)

    # após o stop, o loop interno termina quando chegar a próxima mensagem/timeout (ping)
    run(go())
    assert [t for t, _ in got] == ["order", "wallet"]
    assert ws.sent[0]["op"] == "auth" and ws.sent[0]["args"][0] == "KEY"
    assert ws.sent[0]["args"][2] == ws_signature("SEC", ws.sent[0]["args"][1])
    assert ws.sent[1] == {"op": "subscribe", "args": ["order", "execution", "position", "wallet"]}
    assert ws.closed


def test_reconnects_with_backoff_and_reconciles():
    ws1 = FakeWS([msg("order", [{"x": 1}]), ConnectionError("queda")])
    ws2 = FakeWS([msg("order", [{"x": 2}])])
    conns = [ws1, ws2]
    sleeps, connects, events = [], [], []
    holder = {}

    async def connect(url):
        connects.append(url)
        return conns.pop(0)

    async def on_msg(topic, data):
        events.append(data[0]["x"])
        if data[0]["x"] == 2:
            holder["c"].stop()

    async def on_connect(reconnect):
        connects.append(("on_connect", reconnect))

    async def sleep(s):
        sleeps.append(s)

    async def go():
        c = BybitPrivateWS("wss://x", "K", "S", on_msg, on_connect=on_connect, connect=connect, sleep=sleep,
                           rng=lambda: 0.5, ping_interval=0.05)
        holder["c"] = c
        await asyncio.wait_for(c.run_forever(), 5)
        return c

    c = run(go())
    assert events == [1, 2]
    assert ("on_connect", False) in connects and ("on_connect", True) in connects
    assert len(sleeps) == 1 and sleeps[0] > 0 and c.reconnects == 1
    assert ws1.closed


def test_connect_failures_back_off_exponentially():
    attempts = []
    sleeps = []
    holder = {}

    async def connect(url):
        attempts.append(1)
        if len(attempts) >= 4:
            holder["c"].stop()
        raise OSError("sem rede")

    async def sleep(s):
        sleeps.append(s)

    async def go():
        c = BybitPrivateWS("wss://x", "K", "S", None, connect=connect, sleep=sleep, rng=lambda: 0.5, max_backoff=5)
        holder["c"] = c
        await asyncio.wait_for(c.run_forever(), 5)

    run(go())
    assert sleeps[:2] == [1.0, 2.0]


def test_auth_rejected_raises_and_retries():
    bad = FakeWS([], auto_acks=False)
    bad.incoming = [json.dumps({"success": False, "op": "auth", "ret_msg": "invalid signature"})]
    holder = {}

    async def connect(url):
        holder["c"].stop()
        return bad

    async def sleep(s):
        pass

    async def go():
        c = BybitPrivateWS("wss://x", "K", "S", None, connect=connect, sleep=sleep)
        holder["c"] = c
        await asyncio.wait_for(c.run_forever(), 5)
        return c
    c = run(go())
    assert not c.connected and bad.closed


def test_missing_pong_forces_reconnect():
    ws1 = FakeWS([])     # nunca responde ao ping
    ws2 = FakeWS([msg("order", [{"x": 9}])])
    conns = [ws1, ws2]
    holder, got = {}, []

    async def connect(url):
        return conns.pop(0)

    async def on_msg(t, d):
        got.append(d)
        holder["c"].stop()

    async def sleep(s):
        pass

    async def go():
        c = BybitPrivateWS("wss://x", "K", "S", on_msg, connect=connect, sleep=sleep, ping_interval=0.02)
        holder["c"] = c
        await asyncio.wait_for(c.run_forever(), 5)
        return c
    c = run(go())
    assert got and c.reconnects == 1
    assert any(m.get("op") == "ping" for m in ws1.sent)
