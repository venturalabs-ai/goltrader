import asyncio
import json

import pytest

from brokers import Mode
from core.lease import Lease
from execution import cli, healthcheck
from execution.risk import RiskLimits
from execution.worker import Worker
from test_engine import make
from fakes import FakeBybit


def mkworker(tmp_path, creds=True, **kw):
    eng, f, st = make(tmp_path, RiskLimits(max_order_usd=1e9, max_position_usd=1e9), creds=creds, **kw)
    sdir = tmp_path / "s"
    lease = Lease(sdir / "lease.db", owner="w1", ttl=30)
    assert lease.acquire()
    return Worker(eng, lease, sdir), eng, f, st


def test_tick_writes_heartbeat_and_healthcheck_ok(tmp_path, monkeypatch):
    w, eng, f, st = mkworker(tmp_path)
    asyncio.run(w.tick())
    hb = json.loads((tmp_path / "s" / "heartbeat.json").read_text())
    assert hb["mode"] == "testnet" and hb["lease_ok"] is True and hb["has_credentials"] is True
    ok, msg = healthcheck.check(30)
    assert ok, msg
    ok, msg = healthcheck.check(30, now=hb["ts"] + 100)
    assert not ok and "velho" in msg


def test_healthcheck_without_heartbeat():
    ok, msg = healthcheck.check(30)
    assert not ok


def test_idle_without_credentials_reports(tmp_path):
    w, eng, f, st = mkworker(tmp_path, creds=False)
    asyncio.run(w.tick())
    hb = json.loads((tmp_path / "s" / "heartbeat.json").read_text())
    assert hb["idle_sem_credenciais"] is True and f.calls == []


def test_lease_lost_stops_worker(tmp_path):
    w, eng, f, st = mkworker(tmp_path)
    w.lease.ttl = 1
    other = Lease(tmp_path / "s" / "lease.db", owner="w2", ttl=30)
    import sqlite3
    c = sqlite3.connect(tmp_path / "s" / "lease.db"); c.execute("UPDATE lease SET owner='w2'"); c.commit(); c.close()
    asyncio.run(w.tick())
    assert w.stop_event.is_set() and w.lease_ok is False
    ok, msg = healthcheck.check(30)
    assert not ok and "lease" in msg


def test_commands_submit_kill_reconcile(tmp_path):
    w, eng, f, st = mkworker(tmp_path)
    c1 = st.enqueue("submit", {"category": "linear", "symbol": "BTCUSDT", "side": "buy", "qty": 0.001, "client_id": "cmd1"})
    c2 = st.enqueue("reconcile")
    c3 = st.enqueue("bogus")
    asyncio.run(w.process_commands())
    assert json.loads(st.command(c1)["result"])["link"] == "cmd1" and st.command(c1)["status"] == "done"
    assert st.command(c2)["status"] == "done"
    assert st.command(c3)["status"] == "error"
    f.positions = [{"symbol": "BTCUSDT", "side": "Buy", "size": "0.001", "positionIdx": 0}]
    c4 = st.enqueue("kill", {"reason": "teste"})
    asyncio.run(w.process_commands())
    assert st.command(c4)["status"] == "done" and eng.kill_flag.exists()


def test_tick_executes_external_kill_flag(tmp_path):
    w, eng, f, st = mkworker(tmp_path)
    f.positions = [{"symbol": "BTCUSDT", "side": "Sell", "size": "0.3", "positionIdx": 0}]
    eng.kill_flag.write_text("alguém tocou o arquivo")
    asyncio.run(w.tick())
    closes = [c["body"] for c in f.calls if c["path"] == "/v5/order/create"]
    assert closes and closes[0]["side"] == "Buy" and closes[0]["reduceOnly"] is True


def test_ws_connect_triggers_reconcile(tmp_path):
    w, eng, f, st = mkworker(tmp_path)
    asyncio.run(w.on_ws_connect(True))
    assert any(c["path"] == "/v5/position/list" for c in f.calls)
    asyncio.run(w.on_ws_message("wallet", [{"totalEquity": "5", "coin": []}]))
    assert float(st.kv_get("total_equity")) == 5


def test_run_exits_if_lease_taken(tmp_path):
    eng, f, st = make(tmp_path)
    sdir = tmp_path / "s"
    Lease(sdir / "lease.db", owner="other", ttl=60).acquire()
    w = Worker(eng, Lease(sdir / "lease.db", owner="me", ttl=60), sdir)
    with pytest.raises(SystemExit):
        asyncio.run(w.run())


def test_run_loop_graceful_stop(tmp_path):
    eng, f, st = make(tmp_path)
    sdir = tmp_path / "s"
    w = Worker(eng, Lease(sdir / "lease.db", owner="me", ttl=60), sdir, heartbeat_s=0.01, command_poll_s=0.01)

    async def go():
        t = asyncio.create_task(w.run())
        await asyncio.sleep(0.2)
        w.stop_event.set()
        await asyncio.wait_for(t, 5)
    asyncio.run(go())
    assert Lease(sdir / "lease.db", owner="x").holder() is None      # lease liberado
    assert (sdir / "heartbeat.json").exists()


def test_cli_kill_and_clear(tmp_path, capsys):
    assert cli.main(["kill", "motivo x"]) == 0
    sdir = tmp_path / "s"
    assert (sdir / "KILL_SWITCH").exists()
    assert cli.main(["clear-kill"]) == 2 and (sdir / "KILL_SWITCH").exists()
    assert cli.main(["clear-kill", "--confirm"]) == 0 and not (sdir / "KILL_SWITCH").exists()
    assert cli.main(["submit", "linear", "BTCUSDT", "buy", "0.001", "--client-id", "c1"]) == 0
    assert cli.main(["status"]) == 0


def test_build_engine_defaults_to_testnet(tmp_path, monkeypatch):
    from execution.worker import build_engine
    from core.safety import resolve_mode
    mode = resolve_mode()
    assert mode is Mode.TESTNET
    eng = build_engine(mode, tmp_path / "st", session=FakeBybit())
    assert eng.adapters["linear"].rest.base_url == "https://api-testnet.bybit.com"
