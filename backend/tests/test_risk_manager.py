import json

import pytest

from risk.brokers import PaperBroker
from risk.risk_manager import BRT, InstitutionalRiskManager, Position, RiskConfig, kelly_fraction_full

DAY0 = 1_800_000_000.0  # instante fixo


class Clock:
    def __init__(self, t=DAY0):
        self.t = t

    def __call__(self):
        return self.t


class FakeBroker(PaperBroker):
    def __init__(self):
        self.closed, self.close_all_calls, self.stops = [], 0, []

    async def close_position(self, symbol, side, qty):
        self.closed.append((symbol, side, qty))
        return {"ok": True}

    async def close_all(self):
        self.close_all_calls += 1
        return {"ok": True, "closed": 1}

    async def move_stop(self, symbol, stop_price):
        self.stops.append((symbol, stop_price))
        return {"ok": True}


@pytest.fixture
def mk(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLTRADER_PAPER_EQUITY", "10000")

    def make(**cfg):
        clock = Clock()
        broker = FakeBroker()
        base = dict(trail_pct=0.5, default_sl_pct=1.0, breakeven_trigger_r=1.0, max_daily_drawdown_pct=3.0, min_trades=4)
        base.update(cfg)
        rm = InstitutionalRiskManager(broker=broker, state_dir=str(tmp_path), config=RiskConfig(**base), clock=clock)
        return rm, broker, clock
    return make


# ---------------------------------------------------------------- Kelly
def test_kelly_math():
    assert kelly_fraction_full(0.6, 1.0) == pytest.approx(0.2)
    assert kelly_fraction_full(0.5, 1.0) == 0.0  # sem vantagem
    assert kelly_fraction_full(0.4, 1.0) == 0.0  # negativo vira 0
    assert kelly_fraction_full(0.55, 2.0) == pytest.approx(0.55 - 0.45 / 2)
    assert kelly_fraction_full(0.6, 0.0) == 0.0


def test_win_rate_starts_neutral_and_not_literal(mk):
    rm, *_ = mk()
    assert rm.win_rate_rolling == 0.5 and rm.payoff_ratio == 1.0 and rm.stats_mode == "NEUTRO"
    assert rm.telemetry()["win_rate_rolling"] == 0.5
    assert rm.kelly_full() == 0.0


def _add_trades(rm, pnls, source="BYBIT"):
    for i, p in enumerate(pnls):
        t = {"id": f"t{i}", "ts_open": DAY0, "ts_close": DAY0, "symbol": "BTCUSDT", "side": "BUY", "entry": 1, "exit": 1,
             "qty": 1, "leverage": 1, "pnl_usd": p, "reason": "x", "paper": True, "data_source": source}
        rm.trades.append(t)


def test_rolling_stats_from_history_and_ignores_simulated(mk):
    rm, *_ = mk()
    _add_trades(rm, [10, 10, 10, -5], "SIMULADO")
    assert rm.stats_mode == "NEUTRO"  # simulados não contam
    _add_trades(rm, [10, 10, 10, -5])
    assert rm.stats_mode == "KELLY"
    assert rm.win_rate_rolling == 0.75 and rm.payoff_ratio == pytest.approx(2.0)
    assert rm.kelly_full() == pytest.approx(0.75 - 0.25 / 2)
    assert rm.kelly_applied() == pytest.approx(min(0.625 * 0.25, 0.01))  # limitado a max_risk_per_trade


def test_history_persisted_and_reloaded(mk, tmp_path):
    rm, broker, clock = mk()
    pos = rm.register_position("BTCUSDT", "BUY", 100.0, 1.0, 5, 99.0, True, "BYBIT")
    import asyncio
    asyncio.run(rm.on_price_tick("BTCUSDT", 98.0))  # stop
    assert rm.trades and (tmp_path / "trades.jsonl").exists()
    rm2 = InstitutionalRiskManager(broker=broker, state_dir=str(tmp_path), config=rm.cfg, clock=clock)
    assert len(rm2.trades) == 1 and rm2.trades[0]["id"] == pos.id


def test_sizing_bootstrap_then_kelly_and_refusal(mk):
    rm, *_ = mk()
    p = rm.size_signal("BTCUSDT", "BUY", 50000.0)
    assert p["ok"] and p["sizing_mode"] == "BOOTSTRAP"
    assert p["risk_amount"] == pytest.approx(10000 * 0.001)  # 0.1% do equity
    assert p["stop_loss"] == 49500.0
    # perda no stop == risco: qty * 500 ≈ 10
    assert p["qty"] * 500 <= 10.0001
    _add_trades(rm, [-10, -10, -10, 10])  # Kelly <= 0 => recusa
    r = rm.size_signal("BTCUSDT", "BUY", 50000.0)
    assert not r["ok"] and "Kelly" in r["reason"]
    _add_trades(rm, [20] * 8)
    k = rm.size_signal("BTCUSDT", "BUY", 50000.0)
    assert k["ok"] and k["sizing_mode"] == "KELLY" and k["kelly_applied"] <= 0.01


def test_equity_no_hardcode(mk, monkeypatch):
    rm, *_ = mk()
    assert rm.equity() == 10000.0 and rm.equity_source == "PAPER"
    rm.set_wallet_equity(1234.5, "BYBIT_TESTNET")
    assert rm.equity() == 1234.5 and rm.equity_source == "BYBIT_TESTNET"
    rm.set_wallet_equity(None, None)
    monkeypatch.delenv("GOLTRADER_PAPER_EQUITY")
    assert rm.equity() is None and rm.equity_source == "INDEFINIDO"
    assert not rm.size_signal("BTCUSDT", "BUY", 50000.0)["ok"]


# ---------------------------------------------------------------- PnL com alavancagem real
def test_unrealized_pnl_uses_position_leverage():
    for lev in (1, 3, 10):
        p = Position(id="x", symbol="BTCUSDT", side="BUY", entry=100.0, qty=2.0, leverage=lev, stop_loss=90, initial_sl=90,
                     paper=True, data_source="BYBIT")
        margin = 2.0 * 100 / lev
        assert p.size_usd == pytest.approx(margin)
        assert p.unrealized_pnl(105.0) == pytest.approx(margin * 5 / 100 * lev)  # == qty*(Δpreço)
        assert p.unrealized_pnl(105.0) == pytest.approx(10.0)
    s = Position(id="y", symbol="BTCUSDT", side="SELL", entry=100.0, qty=1.0, leverage=5, stop_loss=110, initial_sl=110,
                 paper=True, data_source="BYBIT")
    assert s.unrealized_pnl(95.0) == pytest.approx(5.0) and s.unrealized_pnl(105.0) == pytest.approx(-5.0)


# ---------------------------------------------------------------- breakeven / trailing
async def test_breakeven_then_trailing_ratchets_long(mk):
    rm, broker, _ = mk()
    pos = rm.register_position("BTCUSDT", "BUY", 100.0, 1.0, 5, 99.0, True, "BYBIT")  # R = 1.0
    await rm.on_price_tick("BTCUSDT", 100.5)
    assert pos.stop_loss == 99.0 and not pos.breakeven_done
    await rm.on_price_tick("BTCUSDT", 101.0)  # +1R -> breakeven (+0.05%)
    assert pos.breakeven_done and pos.stop_loss >= 100.0
    assert pos.stop_loss >= 100.05  # entrada + buffer (trailing pode já ter subido mais)
    await rm.on_price_tick("BTCUSDT", 104.0)  # trailing 0.5% do pico: 104*0.995 = 103.48
    assert pos.stop_loss == pytest.approx(103.48, abs=0.01)
    up = pos.stop_loss
    await rm.on_price_tick("BTCUSDT", 103.6)  # preço recua: stop NÃO desce, e ainda não foi atingido
    assert pos.stop_loss == up and pos.id in rm.positions
    await rm.on_price_tick("BTCUSDT", 103.0)  # cruza o stop -> fecha no lucro
    assert pos.id not in rm.positions
    assert rm.trades[-1]["pnl_usd"] > 0 and rm.trades[-1]["reason"] == "TRAIL"


async def test_breakeven_short_and_stop_loss(mk):
    rm, *_ = mk()
    pos = rm.register_position("BTCUSDT", "SELL", 100.0, 1.0, 5, 101.0, True, "BYBIT")
    await rm.on_price_tick("BTCUSDT", 99.0)  # +1R a favor do short
    assert pos.breakeven_done and pos.stop_loss < 100.0  # stop do short desceu para a entrada ou abaixo
    pos2 = rm.register_position("BTCUSDT", "BUY", 100.0, 1.0, 5, 99.0, True, "BYBIT")
    await rm.on_price_tick("BTCUSDT", 98.9)
    assert pos2.id not in rm.positions and rm.trades[-1]["reason"] == "STOP_LOSS" and rm.trades[-1]["pnl_usd"] < 0


async def test_live_position_stop_move_and_close_go_through_broker(mk):
    rm, broker, clock = mk()
    pos = rm.register_position("BTCUSDT", "BUY", 100.0, 0.5, 5, 99.0, False, "BYBIT")
    await rm.on_price_tick("BTCUSDT", 101.0)
    assert broker.stops and broker.stops[0][0] == "BTCUSDT"
    await rm.on_price_tick("BTCUSDT", 90.0)
    assert broker.closed == [("BTCUSDT", "BUY", 0.5)]  # fechamento via adapter (reduce-only no BybitBroker)
    assert pos.id not in rm.positions


# ---------------------------------------------------------------- circuit breaker
async def test_breaker_trips_on_daily_drawdown_closes_and_resets_manually(mk):
    rm, broker, _ = mk(max_daily_drawdown_pct=3.0)
    pos = rm.register_position("BTCUSDT", "BUY", 100.0, 100.0, 10, 50.0, False, "BYBIT")  # live, stop longe
    assert rm.day_start_equity == 10000.0
    await rm.on_price_tick("BTCUSDT", 99.0)  # -100 USD = -1%
    assert not rm.breaker_tripped
    await rm.on_price_tick("BTCUSDT", 96.0)  # -400 = -4% > 3%
    assert rm.breaker_tripped and "drawdown" in rm.breaker_reason
    assert broker.close_all_calls == 1  # LIVE: fechamento real pelo broker (reduce-only), não só estado local
    assert pos.id not in rm.positions
    with pytest.raises(ValueError):
        rm.reset_breaker("sim")
    with pytest.raises(ValueError):
        rm.reset_breaker("confirmar")
    out = rm.reset_breaker("CONFIRMAR")
    assert out["was_tripped"] and not rm.breaker_tripped


async def test_breaker_paper_positions_closed_locally(mk):
    rm, broker, _ = mk(max_daily_drawdown_pct=1.0)
    rm.register_position("BTCUSDT", "BUY", 100.0, 100.0, 10, 50.0, True, "BYBIT")
    await rm.on_price_tick("BTCUSDT", 97.0)
    assert rm.breaker_tripped and not rm.positions and broker.close_all_calls == 0


async def test_breaker_resets_at_midnight_brt(mk):
    rm, _, clock = mk(max_daily_drawdown_pct=1.0)
    rm.register_position("BTCUSDT", "BUY", 100.0, 100.0, 10, 50.0, True, "BYBIT")
    await rm.on_price_tick("BTCUSDT", 97.0)
    assert rm.breaker_tripped
    day = rm.day
    # avança até 00:00 BRT do dia seguinte
    from datetime import datetime, timedelta
    d = datetime.fromtimestamp(clock.t, BRT)
    nxt = (d + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
    clock.t = nxt.timestamp() - 60
    assert rm.check_day_roll() is False and rm.breaker_tripped  # ainda no mesmo dia (23:59 BRT)
    clock.t = nxt.timestamp()
    assert rm.check_day_roll() is True
    assert rm.day != day and not rm.breaker_tripped
    assert rm.day_start_equity == rm.equity()  # nova âncora


async def test_breaker_state_survives_restart(mk, tmp_path):
    rm, broker, clock = mk(max_daily_drawdown_pct=1.0)
    rm.register_position("BTCUSDT", "BUY", 100.0, 100.0, 10, 50.0, True, "BYBIT")
    await rm.on_price_tick("BTCUSDT", 97.0)
    saved = json.loads((tmp_path / "risk_state.json").read_text())
    assert saved["breaker"]["tripped"] is True
    rm2 = InstitutionalRiskManager(broker=broker, state_dir=str(tmp_path), config=rm.cfg, clock=clock)
    assert rm2.breaker_tripped
