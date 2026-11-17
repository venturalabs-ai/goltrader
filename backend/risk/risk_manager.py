"""InstitutionalRiskManager: Kelly fracionado, breakeven automático, trailing stop e circuit breaker de drawdown diário.

Convenções:
- `size_usd` de uma posição = MARGEM alocada; notional = size_usd × leverage = qty × entry.
- PnL não realizado = size_usd × move_pct/100 × leverage (move_pct a favor da posição) — alavancagem REAL por posição.
- Estatísticas (win rate / payoff) vêm do histórico de trades fechados persistido em disco (jsonl), ignorando trades
  gerados com dados SIMULADOS. Sem histórico suficiente: win_rate 0.5 e payoff 1.0 (neutro) e dimensionamento
  BOOTSTRAP (risco mínimo), nunca um número inventado.
- Equity: saldo real da Bybit (testnet por padrão) quando há chaves; senão GOLTRADER_PAPER_EQUITY rotulado como PAPER;
  sem nenhum dos dois, equity é None e o dimensionamento recusa.
- Circuit breaker: drawdown diário (BRT) >= GOLTRADER_MAX_DAILY_DRAWDOWN_PCT => fecha posições (LIVE: reduce-only via
  corretora), bloqueia ordens e desliga o funil. Reset automático às 00:00 BRT ou manual com "CONFIRMAR".
"""
from __future__ import annotations

import json
import math
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional

from config import env_float, env_int, env_str
from risk.brokers import PaperBroker

BRT = timezone(timedelta(hours=-3))


@dataclass
class RiskConfig:
    kelly_fraction: float = 0.25
    max_risk_per_trade_pct: float = 1.0
    bootstrap_risk_pct: float = 0.1
    min_trades: int = 20
    window: int = 50
    default_sl_pct: float = 1.0
    breakeven_trigger_r: float = 1.0
    breakeven_buffer_pct: float = 0.05
    trail_pct: float = 0.6
    max_daily_drawdown_pct: float = 3.0
    max_margin_frac: float = 0.2
    max_leverage: int = 10
    qty_step: float = 0.001

    @classmethod
    def from_env(cls) -> "RiskConfig":
        return cls(
            kelly_fraction=env_float("GOLTRADER_KELLY_FRACTION", 0.25),
            max_risk_per_trade_pct=env_float("GOLTRADER_MAX_RISK_PER_TRADE_PCT", 1.0),
            bootstrap_risk_pct=env_float("GOLTRADER_BOOTSTRAP_RISK_PCT", 0.1),
            min_trades=env_int("GOLTRADER_KELLY_MIN_TRADES", 20),
            window=env_int("GOLTRADER_KELLY_WINDOW", 50),
            default_sl_pct=env_float("GOLTRADER_DEFAULT_SL_PCT", 1.0),
            breakeven_trigger_r=env_float("GOLTRADER_BREAKEVEN_TRIGGER_R", 1.0),
            breakeven_buffer_pct=env_float("GOLTRADER_BREAKEVEN_BUFFER_PCT", 0.05),
            trail_pct=env_float("GOLTRADER_TRAIL_PCT", 0.6),
            max_daily_drawdown_pct=env_float("GOLTRADER_MAX_DAILY_DRAWDOWN_PCT", 3.0),
            max_margin_frac=env_float("GOLTRADER_MAX_MARGIN_FRAC", 0.2),
            max_leverage=env_int("GOLTRADER_MAX_LEVERAGE", 10),
            qty_step=env_float("GOLTRADER_QTY_STEP", 0.001),
        )


@dataclass
class Position:
    id: str
    symbol: str
    side: str  # "BUY" (long) | "SELL" (short)
    entry: float
    qty: float  # unidades da moeda base
    leverage: int
    stop_loss: float
    initial_sl: float
    paper: bool
    data_source: str
    venue: str = "BYBIT_PERP"
    take_profit: Optional[float] = None
    opened_ts: float = field(default_factory=time.time)
    peak: float = 0.0  # melhor preço a favor
    breakeven_done: bool = False
    stop_kind: str = "SL"  # SL | BE | TRAIL
    last_close_attempt: float = 0.0

    def __post_init__(self):
        if not self.peak:
            self.peak = self.entry

    @property
    def sign(self) -> int:
        return 1 if self.side == "BUY" else -1

    @property
    def size_usd(self) -> float:  # margem
        return self.qty * self.entry / max(self.leverage, 1)

    def move_pct(self, price: float) -> float:
        return (price - self.entry) / self.entry * 100 * self.sign

    def unrealized_pnl(self, price: float) -> float:
        return self.size_usd * self.move_pct(price) / 100 * self.leverage

    @property
    def risk_distance(self) -> float:  # distância do stop inicial (preço)
        return abs(self.entry - self.initial_sl)


def kelly_fraction_full(win_rate: float, payoff_ratio: float) -> float:
    """f* = p − (1−p)/b, com b = ganho médio / perda média. Negativo => sem vantagem (retorna 0.0)."""
    if payoff_ratio <= 0 or not 0 <= win_rate <= 1:
        return 0.0
    return max(0.0, win_rate - (1 - win_rate) / payoff_ratio)


class InstitutionalRiskManager:
    def __init__(
        self,
        broker=None,
        state_dir: Optional[str] = None,
        config: Optional[RiskConfig] = None,
        clock: Callable[[], float] = time.time,
        notifier=None,
        on_realized: Optional[Callable[[float, "Position"], None]] = None,
        on_breaker: Optional[Callable[[bool], None]] = None,
    ):
        self.broker = broker or PaperBroker()
        self.cfg = config or RiskConfig.from_env()
        self.clock = clock
        self.notifier = notifier
        self.on_realized = on_realized
        self.on_breaker = on_breaker
        self.state_dir = Path(state_dir or env_str("GOLTRADER_STATE_DIR", str(Path(__file__).resolve().parent.parent / "state")))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.trades_file = self.state_dir / "trades.jsonl"
        self.state_file = self.state_dir / "risk_state.json"

        self.positions: Dict[str, Position] = {}
        self.prices: Dict[str, float] = {}
        self.events: List[Dict] = []
        self.trades: List[Dict] = self._load_trades()

        self.wallet_equity: Optional[float] = None  # saldo real da corretora
        self.wallet_source: Optional[str] = None
        self.breaker_tripped = False
        self.breaker_reason = ""
        self.breaker_ts: Optional[float] = None
        self._last_breaker_close = 0.0
        self.day: str = self._today()
        self.day_start_equity: Optional[float] = None
        self._load_state()
        self.check_day_roll()

    # ------------------------------------------------------------------ tempo / persistência
    def _today(self) -> str:
        return datetime.fromtimestamp(self.clock(), BRT).strftime("%Y-%m-%d")

    def _load_trades(self) -> List[Dict]:
        out: List[Dict] = []
        if self.trades_file.exists():
            for line in self.trades_file.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out

    def _load_state(self):
        if self.state_file.exists():
            try:
                d = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.day = d.get("day", self.day)
                self.day_start_equity = d.get("day_start_equity")
                b = d.get("breaker", {})
                self.breaker_tripped = bool(b.get("tripped"))
                self.breaker_reason = b.get("reason", "")
                self.breaker_ts = b.get("ts")
            except ValueError:
                pass

    def _save_state(self):
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "day": self.day, "day_start_equity": self.day_start_equity,
            "breaker": {"tripped": self.breaker_tripped, "reason": self.breaker_reason, "ts": self.breaker_ts},
        }), encoding="utf-8")
        os.replace(tmp, self.state_file)

    def _log(self, kind: str, msg: str, **extra):
        ev = {"ts": int(self.clock() * 1000), "kind": kind, "msg": msg, **extra}
        self.events.insert(0, ev)
        del self.events[50:]
        return ev

    async def _notify(self, text: str):
        if self.notifier is not None:
            try:
                await self.notifier.notify(text)
            except Exception:
                pass

    # ------------------------------------------------------------------ equity
    @property
    def paper_base(self) -> Optional[float]:
        v = os.getenv("GOLTRADER_PAPER_EQUITY", "").strip()
        try:
            return float(v) if v else None
        except ValueError:
            return None

    @property
    def paper_realized_total(self) -> float:
        return sum(t["pnl_usd"] for t in self.trades if t.get("paper"))

    @property
    def equity_source(self) -> str:
        if self.wallet_equity is not None:
            return self.wallet_source or "BYBIT"
        return "PAPER" if self.paper_base is not None else "INDEFINIDO"

    def set_wallet_equity(self, value: Optional[float], source: Optional[str]):
        self.wallet_equity, self.wallet_source = value, source
        self.check_day_roll()

    def unrealized_total(self) -> float:
        return sum(p.unrealized_pnl(self.prices.get(p.symbol, p.entry)) for p in self.positions.values())

    def equity(self) -> Optional[float]:
        """Equity atual: carteira real (já inclui não realizado) ou paper = base + realizado + não realizado."""
        if self.wallet_equity is not None:
            return self.wallet_equity
        if self.paper_base is None:
            return None
        return self.paper_base + self.paper_realized_total + self.unrealized_total()

    async def refresh_equity(self):
        try:
            res = await self.broker.get_equity()
        except Exception:
            res = None
        if res:
            self.set_wallet_equity(*res)

    # ------------------------------------------------------------------ dia / drawdown
    def check_day_roll(self) -> bool:
        """Virada do dia BRT (00:00): reancora o equity inicial e reseta o breaker. Também inicializa o 1º dia."""
        today = self._today()
        eq = self.equity()
        rolled = False
        if today != self.day:
            self.day = today
            self.day_start_equity = eq
            if self.breaker_tripped:
                self.breaker_tripped, self.breaker_reason, self.breaker_ts = False, "", None
                self._log("BREAKER_RESET", "reset automático 00:00 BRT")
                if self.on_breaker:
                    self.on_breaker(False)
            rolled = True
            self._save_state()
        elif self.day_start_equity is None and eq is not None:
            self.day_start_equity = eq
            self._save_state()
        return rolled

    def drawdown_pct(self) -> float:
        eq = self.equity()
        if eq is None or not self.day_start_equity or self.day_start_equity <= 0:
            return 0.0
        return max(0.0, (self.day_start_equity - eq) / self.day_start_equity * 100)

    # ------------------------------------------------------------------ estatísticas / Kelly
    def _stats_trades(self) -> List[Dict]:
        valid = [t for t in self.trades if t.get("data_source") != "SIMULADO"]
        return valid[-self.cfg.window:]

    @property
    def stats_mode(self) -> str:
        return "KELLY" if len(self._stats_trades()) >= self.cfg.min_trades else "NEUTRO"

    @property
    def win_rate_rolling(self) -> float:
        tr = self._stats_trades()
        if len(tr) < self.cfg.min_trades:
            return 0.5  # neutro até haver histórico suficiente
        return sum(1 for t in tr if t["pnl_usd"] > 0) / len(tr)

    @property
    def payoff_ratio(self) -> float:
        tr = self._stats_trades()
        if len(tr) < self.cfg.min_trades:
            return 1.0
        wins = [t["pnl_usd"] for t in tr if t["pnl_usd"] > 0]
        losses = [-t["pnl_usd"] for t in tr if t["pnl_usd"] < 0]
        if not wins or not losses:
            return 1.0
        return (sum(wins) / len(wins)) / (sum(losses) / len(losses))

    def kelly_full(self) -> float:
        return kelly_fraction_full(self.win_rate_rolling, self.payoff_ratio)

    def kelly_applied(self) -> float:
        return min(self.kelly_full() * self.cfg.kelly_fraction, self.cfg.max_risk_per_trade_pct / 100)

    def size_signal(self, symbol: str, side: str, price: float, sl_pct: Optional[float] = None,
                    leverage: Optional[int] = None) -> Dict:
        """Dimensiona uma entrada. Com histórico insuficiente usa risco BOOTSTRAP mínimo; com Kelly = 0 recusa."""
        eq = self.equity()
        if eq is None or eq <= 0:
            return {"ok": False, "reason": "equity indefinido (sem saldo Bybit nem GOLTRADER_PAPER_EQUITY)"}
        if price <= 0:
            return {"ok": False, "reason": "preço inválido"}
        sl_pct = sl_pct or self.cfg.default_sl_pct
        lev = min(leverage or 5, self.cfg.max_leverage)
        if self.stats_mode == "NEUTRO":
            frac, mode = self.cfg.bootstrap_risk_pct / 100, "BOOTSTRAP"
        else:
            frac, mode = self.kelly_applied(), "KELLY"
        if frac <= 0:
            return {"ok": False, "reason": "Kelly sem vantagem estatística (f*=0): entrada recusada", "sizing_mode": mode}
        risk_amount = eq * frac
        notional = risk_amount / (sl_pct / 100)  # perda no stop == risk_amount
        notional = min(notional, eq * self.cfg.max_margin_frac * lev)  # teto de margem
        qty = math.floor(notional / price / self.cfg.qty_step) * self.cfg.qty_step
        qty = round(qty, 8)
        if qty < self.cfg.qty_step:
            return {"ok": False, "reason": "quantidade abaixo do passo mínimo", "sizing_mode": mode}
        d = price * sl_pct / 100
        sl = price - d if side == "BUY" else price + d
        return {
            "ok": True, "symbol": symbol, "side": side, "qty": qty, "leverage": lev, "stop_loss": round(sl, 2),
            "risk_amount": round(risk_amount, 4), "sizing_mode": mode, "kelly_full": round(self.kelly_full(), 4),
            "kelly_applied": round(frac, 6), "equity": round(eq, 2), "equity_source": self.equity_source,
        }

    def default_stop(self, side: str, price: float) -> float:
        d = price * self.cfg.default_sl_pct / 100
        return round(price - d if side == "BUY" else price + d, 2)

    # ------------------------------------------------------------------ posições
    def register_position(self, symbol: str, side: str, entry: float, qty: float, leverage: int,
                          stop_loss: Optional[float], paper: bool, data_source: str,
                          take_profit: Optional[float] = None, venue: str = "BYBIT_PERP") -> Position:
        sl = stop_loss or self.default_stop(side, entry)
        pos = Position(id=f"P-{uuid.uuid4().hex[:10]}", symbol=symbol, side=side, entry=entry, qty=qty,
                       leverage=max(int(leverage or 1), 1), stop_loss=sl, initial_sl=sl, paper=paper,
                       data_source=data_source, take_profit=take_profit, venue=venue, opened_ts=self.clock())
        self.positions[pos.id] = pos
        self._log("OPEN", f"{side} {qty} {symbol} @ {entry} SL {sl}", position=pos.id)
        return pos

    async def on_price_tick(self, symbol: str, price: float) -> List[Dict]:
        """Chamado a cada tick de preço: breakeven, trailing, saídas, drawdown e circuit breaker."""
        events: List[Dict] = []
        if price <= 0:
            return events
        self.prices[symbol] = price
        self.check_day_roll()
        for pos in list(self.positions.values()):
            if pos.symbol != symbol:
                continue
            pos.peak = max(pos.peak, price) if pos.sign == 1 else min(pos.peak, price)
            favor = (pos.peak - pos.entry) * pos.sign
            # breakeven: ao atingir N×R a favor, stop vai para a entrada (+ buffer de custos)
            if not pos.breakeven_done and pos.risk_distance > 0 and favor >= self.cfg.breakeven_trigger_r * pos.risk_distance:
                be = pos.entry * (1 + pos.sign * self.cfg.breakeven_buffer_pct / 100)
                if (be - pos.stop_loss) * pos.sign > 0:
                    await self._move_stop(pos, round(be, 2), "BE", events)
                pos.breakeven_done = True
            # trailing: só depois do breakeven, distância fixa % do pico; só avança a favor
            if pos.breakeven_done:
                trail = pos.peak * (1 - pos.sign * self.cfg.trail_pct / 100)
                if (trail - pos.stop_loss) * pos.sign > 0:
                    await self._move_stop(pos, round(trail, 2), "TRAIL", events)
            # saídas
            if (price - pos.stop_loss) * pos.sign <= 0:
                await self._close(pos, price, pos.stop_kind if pos.stop_kind != "SL" else "STOP_LOSS", events)
            elif pos.take_profit and (price - pos.take_profit) * pos.sign >= 0:
                await self._close(pos, price, "TAKE_PROFIT", events)
        if self.breaker_tripped:  # breaker ativo: reenvia fechamento LIVE pendente (reduce-only) a cada 5 s
            live = [p for p in self.positions.values() if not p.paper]
            if live and self.clock() - self._last_breaker_close >= 5:
                self._last_breaker_close = self.clock()
                r = await self.broker.close_all()
                if r.get("ok"):
                    for p in live:
                        self._record_forced(p, p.unrealized_pnl(self.prices.get(p.symbol, p.entry)))
                    events.append(self._log("BREAKER_CLOSE_ALL", "fechamento pendente concluído"))
        else:
            dd = self.drawdown_pct()
            if dd >= self.cfg.max_daily_drawdown_pct:
                events += await self.trip_breaker(f"drawdown diário {dd:.2f}% >= {self.cfg.max_daily_drawdown_pct:.2f}%")
        return events

    async def _move_stop(self, pos: Position, new_sl: float, kind: str, events: List[Dict]):
        old = pos.stop_loss
        if not pos.paper:
            r = await self.broker.move_stop(pos.symbol, new_sl)
            if not r.get("ok"):
                events.append(self._log("STOP_MOVE_FAILED", f"{pos.symbol}: {r.get('retMsg')}", position=pos.id))
        pos.stop_loss, pos.stop_kind = new_sl, kind
        events.append(self._log(kind, f"{pos.symbol} stop {old} -> {new_sl}", position=pos.id))

    async def _close(self, pos: Position, price: float, reason: str, events: List[Dict]) -> bool:
        if not pos.paper:  # LIVE: fechamento real reduce-only pela corretora
            if self.clock() - pos.last_close_attempt < 5:
                return False
            pos.last_close_attempt = self.clock()
            r = await self.broker.close_position(pos.symbol, pos.side, pos.qty)
            if not r.get("ok"):
                events.append(self._log("CLOSE_FAILED", f"{pos.symbol}: {r.get('retMsg')}", position=pos.id))
                return False
        pnl = pos.unrealized_pnl(price)
        trade = {
            "id": pos.id, "ts_open": pos.opened_ts, "ts_close": self.clock(), "symbol": pos.symbol, "side": pos.side,
            "entry": pos.entry, "exit": price, "qty": pos.qty, "leverage": pos.leverage, "pnl_usd": round(pnl, 6),
            "reason": reason, "paper": pos.paper, "data_source": pos.data_source,
        }
        self.trades.append(trade)
        with self.trades_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(trade) + "\n")
        self.positions.pop(pos.id, None)
        events.append(self._log("CLOSE", f"{pos.symbol} {reason} pnl {pnl:+.2f}", position=pos.id))
        if self.on_realized and not pos.paper:
            self.on_realized(pnl, pos)
        await self._notify(f"Posição fechada {pos.symbol} {reason} PnL {pnl:+.2f} USD ({'PAPER' if pos.paper else 'LIVE'})")
        return True

    # ------------------------------------------------------------------ circuit breaker
    async def trip_breaker(self, reason: str) -> List[Dict]:
        events: List[Dict] = []
        self.breaker_tripped, self.breaker_reason, self.breaker_ts = True, reason, self.clock()
        self._save_state()
        events.append(self._log("BREAKER_TRIP", reason))
        if self.on_breaker:
            self.on_breaker(True)
        live = [p for p in self.positions.values() if not p.paper]
        if live:  # LIVE: fecha na corretora (reduce-only) tudo que a conta tiver
            r = await self.broker.close_all()
            events.append(self._log("BREAKER_CLOSE_ALL", f"fechamento reduce-only: {r}"))
            if r.get("ok"):
                for p in live:
                    pnl = p.unrealized_pnl(self.prices.get(p.symbol, p.entry))
                    self._record_forced(p, pnl)
        for p in [p for p in self.positions.values() if p.paper]:
            self._record_forced(p, p.unrealized_pnl(self.prices.get(p.symbol, p.entry)))
        await self._notify(f"CIRCUIT BREAKER acionado: {reason}")
        return events

    def _record_forced(self, pos: Position, pnl: float):
        price = self.prices.get(pos.symbol, pos.entry)
        trade = {
            "id": pos.id, "ts_open": pos.opened_ts, "ts_close": self.clock(), "symbol": pos.symbol, "side": pos.side,
            "entry": pos.entry, "exit": price, "qty": pos.qty, "leverage": pos.leverage, "pnl_usd": round(pnl, 6),
            "reason": "BREAKER", "paper": pos.paper, "data_source": pos.data_source,
        }
        self.trades.append(trade)
        with self.trades_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(trade) + "\n")
        self.positions.pop(pos.id, None)
        if self.on_realized and not pos.paper:
            self.on_realized(pnl, pos)

    def reset_breaker(self, confirm: str) -> Dict:
        """Reset manual: exige 'CONFIRMAR'. Reancora o equity inicial do dia no equity atual (evita re-disparo imediato)."""
        if confirm != "CONFIRMAR":
            raise ValueError('reset exige confirm="CONFIRMAR"')
        was = self.breaker_tripped
        self.breaker_tripped, self.breaker_reason, self.breaker_ts = False, "", None
        self.day_start_equity = self.equity()
        self._save_state()
        self._log("BREAKER_RESET", "reset manual (CONFIRMAR)")
        if self.on_breaker:
            self.on_breaker(False)
        return {"was_tripped": was, "day_start_equity": self.day_start_equity}

    # ------------------------------------------------------------------ telemetria (sem segredos)
    def telemetry(self) -> Dict:
        eq = self.equity()
        realized_today = sum(
            t["pnl_usd"] for t in self.trades
            if datetime.fromtimestamp(t["ts_close"], BRT).strftime("%Y-%m-%d") == self.day
        )
        return {
            "equity": None if eq is None else round(eq, 2),
            "equity_source": self.equity_source,
            "equity_label": {"PAPER": "PAPER (GOLTRADER_PAPER_EQUITY)", "BYBIT_TESTNET": "Bybit TESTNET (saldo real da testnet)",
                             "BYBIT_REAL": "Bybit CONTA REAL", "INDEFINIDO": "indefinido — defina GOLTRADER_PAPER_EQUITY ou chaves Bybit"
                             }.get(self.equity_source, self.equity_source),
            "day": self.day,
            "day_start_equity": None if self.day_start_equity is None else round(self.day_start_equity, 2),
            "drawdown_pct": round(self.drawdown_pct(), 3),
            "max_daily_drawdown_pct": self.cfg.max_daily_drawdown_pct,
            "breaker": {"tripped": self.breaker_tripped, "reason": self.breaker_reason, "ts": self.breaker_ts},
            "win_rate_rolling": round(self.win_rate_rolling, 4),
            "payoff_ratio": round(self.payoff_ratio, 3),
            "stats_mode": self.stats_mode,
            "trades_in_window": len(self._stats_trades()),
            "min_trades_for_kelly": self.cfg.min_trades,
            "kelly_full": round(self.kelly_full(), 4),
            "kelly_fraction": self.cfg.kelly_fraction,
            "kelly_applied": round(self.kelly_applied(), 6),
            "unrealized_pnl": round(self.unrealized_total(), 2),
            "realized_today": round(realized_today, 2),
            "positions": [
                {**asdict(p), "size_usd": round(p.size_usd, 2), "unrealized_pnl": round(p.unrealized_pnl(self.prices.get(p.symbol, p.entry)), 2),
                 "move_pct": round(p.move_pct(self.prices.get(p.symbol, p.entry)), 3)}
                for p in self.positions.values()
            ],
            "events": self.events[:12],
        }
