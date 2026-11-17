r"""Goltrader Autotrader 24/7 — worker headless (independe do navegador).

Uso:
  .venv\Scripts\pythonw.exe autotrader.py            -> roda 24/7 (usado pela tarefa agendada)
  .venv\Scripts\python.exe  autotrader.py --dry-run --loops 2 [--trials 3] [--symbols DOGEUSDT,XRPUSDT]
  .venv\Scripts\python.exe  autotrader.py --start | --stop | --status

SPOT (long-only): CALL = compra a mercado; saida = venda da quantidade que o PROPRIO robo comprou
(liquida de taxas, arredondada para baixo no basePrecision). PUT so serve para sair antes.
Tamanho = minimo da Bybit + extra_usdt (0,15) com qty arredondada PARA CIMA no basePrecision.
Entrada so com metodologia validada: retorno medio REAL das compras na janela fora-da-amostra,
liquido de taxas (ida+volta), acima do minimo. Detectores de oportunidade reforcam o score.
Sem limites por padrao (0 = sem limite). Kill switch, CONFIRMAR e saldo insuficiente sempre valem.
"""
import argparse
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from decimal import Decimal, ROUND_UP
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

import pandas as pd  # noqa: E402

from core.hybrid_memory import HybridMemoryStore  # noqa: E402
from dashboard_components import autostate as A  # noqa: E402
from dashboard_components import engine as E  # noqa: E402
from dashboard_components import keys  # noqa: E402
from dashboard_components import opportunity as O  # noqa: E402
from dashboard_components import risk as R  # noqa: E402
from dashboard_components.bybit_client import BybitClient, fetch_candles, round_down  # noqa: E402

LOG = logging.getLogger("autotrader")
LOCK_PORT = 47651
INTERVAL_MIN = {"1": 1, "3": 3, "5": 5, "15": 15, "30": 30, "60": 60, "120": 120, "240": 240, "D": 1440}


class _Scrub(logging.Filter):
    def filter(self, record):
        record.msg = keys.scrub(record.getMessage())
        record.args = ()
        return True


def setup_log(console: bool):
    LOG.setLevel(logging.INFO)
    fh = RotatingFileHandler(R.LOG_DIR / "autotrader.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    fh.addFilter(_Scrub())
    LOG.addHandler(fh)
    if console and sys.stdout:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        sh.addFilter(_Scrub())
        LOG.addHandler(sh)


def min_order(inst: dict, price: float, extra: float) -> dict:
    """Minimo da Bybit (max(minOrderQty*preco, minNotional)) + extra, qty arredondada PARA CIMA no qtyStep."""
    step = Decimal(str(inst["qty_step"]))
    p = Decimal(str(price))
    min_req = max(Decimal(str(inst["min_qty"])) * p, Decimal(str(inst["min_notional"])))
    target = min_req + Decimal(str(extra))
    qty = (target / p / step).to_integral_value(rounding=ROUND_UP) * step
    min_q = (Decimal(str(inst["min_qty"])) / step).to_integral_value(rounding=ROUND_UP) * step
    qty = max(qty, min_q)
    while qty * p < Decimal(str(inst["min_notional"])):
        qty += step
    return {"qty": format(qty.normalize(), "f"), "notional": float(qty * p), "min_req": float(min_req),
            "target": float(target)}


class Worker:
    CAT = "spot"

    def __init__(self, dry_cli: bool = False, loops: int = 0, symbols=None, trials=None):
        self.dry_cli, self.max_loops, self.sym_override, self.trials_override = dry_cli, loops, symbols, trials
        self.rt_path = A.STATE_DIR / ("autotrader_runtime_dryrun.json" if dry_cli else "autotrader_runtime.json")
        old = A.load_runtime(self.rt_path)
        keep_open = [] if dry_cli else [t for t in old.get("open", []) if t.get("category") == "spot"]
        self.rt = {"open": keep_open, "done": [d for d in old.get("done", []) if d.get("category") == "spot"][-500:],
                   "entries": old.get("entries", [])[-500:], "events": old.get("events", [])[-100:],
                   "symbols": {}, "opportunities": [], "started_at": time.time(), "pid": os.getpid(), "category": "spot"}
        self.champs = {k: v for k, v in A.load_champs().items() if v.get("category") == "spot"}
        self.client, self.client_key = None, None
        self.instruments, self.inst_ts = {}, 0.0
        self.prices = {}
        self.processed = {}
        self.lock = threading.Lock()
        self.stop = False
        self.env_mtime = 0.0

    # ---------------- helpers
    def ev(self, msg, level=logging.INFO):
        LOG.log(level, msg)
        self.rt["events"].append({"ts": pd.Timestamp.now(tz="America/Sao_Paulo").strftime("%d/%m %H:%M:%S"), "msg": keys.scrub(msg)})
        del self.rt["events"][:-100]

    def get_client(self, st):
        envf = ROOT / ".env"
        mt = envf.stat().st_mtime if envf.exists() else 0
        if mt != self.env_mtime:
            load_dotenv(envf, override=True)
            self.env_mtime = mt
            self.client = None
        mode = keys.REAL if st["env"] == "real" else keys.TESTNET
        if self.client is None or self.client_key != mode:
            self.client, self.client_key = BybitClient(mode), mode
        return self.client, mode

    def universe(self, st):
        return self.sym_override or [s.strip().upper() for s in st["universe"] if s.strip()]

    def fee(self, st):
        return float(st.get("fee_pct", 0.1) or 0.0)

    def save_rt(self, st, mode):
        self.rt.update({"heartbeat": time.time(), "env": st["env"], "enabled": st["enabled"],
                        "armed": st["real_armed"], "dry_run": bool(st["dry_run"] or self.dry_cli), "mode": mode})
        with self.lock:
            self.rt["champions"] = {k: {kk: v.get(kk) for kk in ("strategy", "long_net_pct", "long_ret_pct", "long_n",
                                                                  "trained_at", "n_validated")}
                                    for k, v in self.champs.items()}
        A.save_runtime(self.rt, self.rt_path)

    # ---------------- treino (thread)
    def train(self, sym, st):
        payout = float(os.getenv("DEFAULT_PAYOUT", "0.85") or 0.85)
        df = fetch_candles(sym, st["interval"], "spot", keys.REAL, 1000)
        mem = HybridMemoryStore()
        mem.optuna_storage_url = None
        n = int(self.trials_override or st["n_trials"])
        exp = int(st["expiry_candles"])
        res = E.run_tournament(df, sym, st["interval"], payout, exp, n, memory=mem)
        df_oos = df.iloc[int(len(df) * 0.70):]
        board = []
        for r in res["leaderboard"]:
            row = {k: r[k] for k in ("strategy", "trades", "wins", "win_rate", "EV_por_trade", "p_value",
                                     "aprovado_bonferroni", "best_params")}
            row.update(O.long_stats(df_oos, r["strategy"], r.get("best_params"), exp, self.fee(st)))
            board.append(row)
        valid = [r for r in board if r["long_n"] >= int(st["min_long_trades"]) and r["long_net_pct"] > float(st["min_net_edge_pct"])]
        best = max(board, key=lambda r: (r["long_n"] >= int(st["min_long_trades"]), r["long_net_pct"]))
        with self.lock:
            self.champs[sym] = {"strategy": best["strategy"], "long_net_pct": best["long_net_pct"],
                                "long_ret_pct": best["long_ret_pct"], "long_n": best["long_n"], "board": board,
                                "interval": st["interval"], "expiry": exp, "category": "spot", "trained_at": time.time(),
                                "n_validated": len(valid)}
            allc = A.load_champs()
            allc.update(self.champs)
            A.save_champs(allc)
        self.ev(f"Torneio SPOT {sym} {st['interval']}m: melhor compra {best['strategy']} edge bruto {best['long_ret_pct']:+.3f}% "
                f"líquido {best['long_net_pct']:+.3f}% ({best['long_n']} compras OOS) · {len(valid)} validada(s) após taxas")

    def trainer_loop(self):
        while not self.stop:
            try:
                st = A.load_state()
                due = []
                for s in self.universe(st):
                    if not self.rt["symbols"].get(s, {}).get("eligible"):
                        continue
                    c = self.champs.get(s, {})
                    age = time.time() - float(c.get("trained_at", 0))
                    if c.get("interval") != st["interval"] or c.get("expiry") != int(st["expiry_candles"]) \
                            or age > float(st["retrain_every_min"]) * 60:
                        due.append((age, s))
                if due:
                    due.sort(reverse=True)
                    self.train(due[0][1], st)
                    continue
            except Exception as e:
                self.ev(f"Erro no treino: {e}", logging.WARNING)
                time.sleep(10)
            time.sleep(2)

    # ---------------- universo / elegibilidade
    def refresh_universe(self, client, st, usdt_free):
        if time.time() - self.inst_ts > 600 or not self.instruments:
            for s in self.universe(st):
                try:
                    self.instruments[s] = client.instrument("spot", s)
                except Exception as e:
                    self.ev(f"Instrumento spot {s} indisponível: {e}", logging.WARNING)
            self.inst_ts = time.time()
        tick = {t["symbol"]: t for t in client._call("get_tickers", category="spot").get("list", [])}
        for s in self.universe(st):
            inst, tk = self.instruments.get(s), tick.get(s)
            if not inst or not tk:
                continue
            px = float(tk["lastPrice"])
            self.prices[s] = px
            mo = min_order(inst, px, float(st["extra_usdt"]))
            cost = mo["notional"] * (1 + self.fee(st) / 100)
            cap = float(st["max_stake_pct"] or 0)
            reason = ""
            if usdt_free is not None and cost > usdt_free:
                reason = f"saldo USDT insuficiente ({usdt_free:.2f} < {cost:.2f})"
            elif cap > 0 and usdt_free is not None and cost > usdt_free * cap / 100:
                reason = f"acima do limite {cap}% por trade"
            self.rt["symbols"][s] = {"price": px, "min_qty": inst["min_qty"], "qty_step": inst["qty_step"],
                                     "min_notional": inst["min_notional"], "min_req": round(mo["min_req"], 4),
                                     "order_qty": mo["qty"], "order_usdt": round(mo["notional"], 4),
                                     "margin": round(cost, 4), "eligible": not reason, "why": reason}

    # ---------------- posicoes do robo
    def bot_pos(self, s, mode):
        return next((t for t in self.rt["open"] if t["symbol"] == s and t["mode"] == mode), None)

    def sell_position(self, client, mode, st, tr, why):
        s = tr["symbol"]
        inst = self.instruments.get(s) or client.instrument("spot", s)
        px = self.prices.get(s) or client.ticker_price("spot", s)
        base = s[:-4] if s.endswith("USDT") else s
        now = pd.Timestamp.now(tz="UTC")
        fee = self.fee(st)
        if tr.get("simulated"):
            proceeds = float(tr["net_qty"]) * px * (1 - fee / 100)
            self.finish(tr, proceeds - float(tr["cost_usdt"]), px, why, now)
            return
        free = client.coin_free(base)
        qty = float(round_down(min(float(tr["net_qty"]), free), inst["qty_step"]))
        if qty <= 0 or qty < inst["min_qty"] or qty * px < inst["min_notional"]:
            self.ev(f"POEIRA {s}: {qty} {base} (≈{qty * px:.4f} USDT) abaixo do mínimo de venda — deixado na carteira", logging.WARNING)
            self.finish(tr, None, px, f"{why} / poeira", now)
            return
        ok, msg, res = R.guarded_order(client, mode=mode, real_armed=True, risk={}, source="autotrader_saida", category="spot",
                                       symbol=s, side="Sell", qty=round_down(qty, inst["qty_step"]), reduce_only=True)
        if not ok:
            tr["close_fail"] = tr.get("close_fail", 0) + 1
            self.ev(f"Venda {s} falhou ({tr['close_fail']}x): {msg}", logging.WARNING)
            return
        fills = {}
        for _ in range(5):
            time.sleep(0.8)
            try:
                fills = client.spot_fills(s, res.get("orderId"))
                if fills.get("qty"):
                    break
            except Exception:
                pass
        proceeds = fills.get("value", qty * px) - fills.get("fees", {}).get("USDT", 0.0)
        self.finish(tr, proceeds - float(tr["cost_usdt"]), fills.get("avg") or px, why, now, sell_id=res.get("orderId"))

    def finish(self, tr, pnl, exit_px, why, now, sell_id=None):
        self.rt["open"].remove(tr)
        outcome = None if pnl is None else int(pnl > 0)
        self.rt["done"].append({**tr, "closed_at": now.isoformat(), "exit_price": exit_px, "pnl": None if pnl is None else round(pnl, 6),
                                "outcome": outcome, "exit_reason": why, "sell_orderId": sell_id})
        del self.rt["done"][:-500]
        if not tr.get("simulated"):
            try:
                HybridMemoryStore().log_trade(tr["symbol"], tr["interval"], tr["strategy"], "CALL", float(tr["cost_usdt"]),
                                              0.0, f"{tr['mode']}-spot-autotrader", outcome,
                                              {"buy": tr.get("orderId"), "sell": sell_id, "pnl": pnl})
            except Exception:
                pass
        self.ev(f"{'SAÍDA SIMULADA' if tr.get('simulated') else 'VENDA'} {tr['symbol']} ({why}) @ {exit_px} PnL "
                f"{'—' if pnl is None else f'{pnl:+.4f} USDT'}")

    def manage_exits(self, client, mode, st):
        now = pd.Timestamp.now(tz="UTC")
        tp, sl = float(st["tp_pct"] or 0), float(st["sl_pct"] or 0)
        for tr in list(self.rt["open"]):
            if tr["mode"] != mode:
                continue
            px = self.prices.get(tr["symbol"])
            why = None
            if tr.get("exit_now"):
                why = tr["exit_now"]
            elif now >= pd.Timestamp(tr["close_at"]):
                why = "expiração"
            elif px and tp > 0 and px >= float(tr["entry_price"]) * (1 + tp / 100):
                why = f"take profit {tp}%"
            elif px and sl > 0 and px <= float(tr["entry_price"]) * (1 - sl / 100):
                why = f"stop loss {sl}%"
            if why:
                try:
                    self.sell_position(client, mode, st, tr, why)
                except Exception as e:
                    self.ev(f"Erro na venda {tr['symbol']}: {e}", logging.WARNING)

    # ---------------- scanner + entradas
    def scan_and_enter(self, client, mode, st, usdt_free, force_eval=False):
        mins = INTERVAL_MIN[st["interval"]]
        now = time.time()
        bar_start = pd.Timestamp(int(now // (mins * 60)) * mins * 60, unit="s", tz="UTC")
        if not force_eval and (now - bar_start.timestamp()) < 2:
            return
        fee_rt = 2 * self.fee(st)

        def eval_one(s):
            info = self.rt["symbols"].get(s)
            ch = self.champs.get(s)
            if not info:
                return None
            if not force_eval and self.processed.get(s) == str(bar_start):
                return None
            row = {"symbol": s, "price": info["price"], "order_qty": info["order_qty"], "order_usdt": info["order_usdt"],
                   "eligible": info["eligible"], "champion": (ch or {}).get("strategy"), "score": 0.0, "decision": "HOLD",
                   "hits": "", "reason": "", "best": None, "bar": str(bar_start), "taxa_rt_pct": fee_rt,
                   "edge_bruto_pct": None, "edge_liq_pct": None}
            if not ch or ch.get("interval") != st["interval"]:
                row["reason"] = "aguardando torneio"
                return row
            try:
                df = fetch_candles(s, st["interval"], "spot", keys.REAL, 400)
            except Exception as e:
                row["reason"] = f"candles: {e}"
                return row
            if not force_eval and df.index[-1] != bar_start:
                return None
            row["bar"] = str(df.index[-1])
            self.processed[s] = str(bar_start)
            ob = None
            if st["use_orderbook"]:
                try:
                    bk = client._call("get_orderbook", category="spot", symbol=s, limit=50)
                    sb = sum(float(x[1]) * float(x[0]) for x in bk.get("b", []))
                    sa = sum(float(x[1]) * float(x[0]) for x in bk.get("a", []))
                    ob = (sb - sa) / (sb + sa) if (sb + sa) else None
                except Exception:
                    ob = None
            det = O.detectors(df, None, ob)
            evl = O.evaluate_long(df, ch["board"], det, min_net=float(st["min_net_edge_pct"]),
                                  min_long_trades=int(st["min_long_trades"]), require_bonf=bool(st["require_bonferroni"]),
                                  has_position=self.bot_pos(s, mode) is not None)
            b = evl.get("best") or {}
            row.update({"hits": " / ".join(det["hits"]) or "—", "rsi": det["rsi"], "ret15": det["ret15"], "z_vwap": det["z_vwap"],
                        "score": evl["score"], "decision": evl["decision"], "reason": evl["reason"],
                        "methods": len(evl.get("methods", [])), "best": b.get("strategy"),
                        "edge_bruto_pct": b.get("gross", ch.get("long_ret_pct")), "edge_liq_pct": b.get("net", ch.get("long_net_pct"))})
            if evl["decision"] == "CALL" and evl["score"] <= float(st["score_min"]):
                row["decision"], row["reason"] = "HOLD", f"score {evl['score']:.4f} ≤ mínimo {st['score_min']}"
            return row

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            opps = [r for r in ex.map(eval_one, self.universe(st)) if r]
        if not opps:
            return
        prev = {o["symbol"]: o for o in self.rt.get("opportunities", [])}
        prev.update({o["symbol"]: o for o in opps})
        self.rt["opportunities"] = sorted(prev.values(), key=lambda o: o.get("score") or 0, reverse=True)

        for o in opps:  # PUT validado com posicao do robo -> saida antecipada
            if o["decision"] == "EXIT":
                tr = self.bot_pos(o["symbol"], mode)
                if tr:
                    tr["exit_now"] = "sinal de venda validado (saída antecipada)"
            elif "PUT ignorado" in (o.get("reason") or ""):
                LOG.info(f"{o['symbol']}: PUT ignorado (spot não vende a descoberto)")
        if any(t.get("exit_now") for t in self.rt["open"]):
            self.manage_exits(client, mode, st)

        cands = sorted([o for o in opps if o["decision"] == "CALL"], key=lambda o: o["score"], reverse=True)
        if not cands:
            return
        blocked = self.trading_block(client, mode, st)
        dry = bool(st["dry_run"] or self.dry_cli)
        if blocked and not dry:
            self.rt["last_reason"] = blocked
            self.ev(f"{len(cands)} oportunidade(s) de compra não operada(s) — motivo: {blocked} · "
                    + ", ".join(f"{o['symbol']} {o['score']:.3f}" for o in cands[:6]))
            return
        budget = usdt_free if usdt_free is not None else (1e9 if dry else 0.0)
        hour_ago = time.time() - 3600
        n_hour = len([t for t in self.rt["entries"] if t > hour_ago])
        n_open = len([t for t in self.rt["open"] if t["mode"] == mode])
        for o in cands:
            s = o["symbol"]
            info = self.rt["symbols"][s]
            tag = f"{s} COMPRA score {o['score']:.4f} ({o['best']}; edge líq {o['edge_liq_pct']:+.3f}%; {o['hits']})"
            why = None
            if not info["eligible"]:
                why = info["why"]
            elif self.bot_pos(s, mode):
                why = "robô já tem posição neste par"
            elif int(st["max_open_positions"] or 0) > 0 and n_open >= int(st["max_open_positions"]):
                why = "limite de posições"
            elif int(st["max_trades_per_hour"] or 0) > 0 and n_hour >= int(st["max_trades_per_hour"]):
                why = "limite de trades/hora"
            elif float(st["cooldown_min"] or 0) > 0 and any(
                    d["symbol"] == s and time.time() - pd.Timestamp(d["opened_at"]).timestamp() < float(st["cooldown_min"]) * 60
                    for d in self.rt["done"][-50:]):
                why = "cooldown do par"
            elif float(st["max_daily_loss_usdt"] or 0) > 0 and (self.rt.get("pnl_today") or 0) <= -float(st["max_daily_loss_usdt"]):
                why = "limite de perda diária"
            elif info["margin"] > budget:
                why = f"saldo USDT insuficiente ({budget:.2f} < {info['margin']:.2f})"
            if why:
                self.ev(f"PULADO {tag}: {why}")
                self.rt["last_reason"] = f"{s}: {why}"
                if "insuficiente" in why:
                    break
                continue
            ok, msg = self.enter(client, mode, st, s, info, o, dry, mins)
            if ok:
                budget -= info["margin"]
                n_hour += 1
                n_open += 1
                self.rt["entries"].append(time.time())
                del self.rt["entries"][:-500]
                self.ev(f"{'COMPRA SIMULADA' if dry else 'COMPRA'} {tag} qtd {info['order_qty']} ≈ {info['order_usdt']:.2f} USDT: {msg}")
            else:
                self.rt["last_reason"] = f"{s}: {msg}"
                self.ev(f"NÃO ENVIADA {tag}: {msg}", logging.WARNING)
                if "insuficiente" in msg or "170131" in msg:
                    break

    def trading_block(self, client, mode, st):
        if R.kill_active():
            return "kill switch"
        if not st["enabled"]:
            return "autônomo desligado"
        if mode == keys.REAL and not st["real_armed"]:
            return "conta real não confirmada"
        if not client.authenticated:
            return "chave inválida (não configurada)"
        return None

    def enter(self, client, mode, st, s, info, o, dry, mins):
        base = s[:-4] if s.endswith("USDT") else s
        fee = self.fee(st)
        close_at = pd.Timestamp(o["bar"]) + pd.Timedelta(minutes=mins * int(st["expiry_candles"]))
        if close_at <= pd.Timestamp.now(tz="UTC"):
            close_at = pd.Timestamp.now(tz="UTC") + pd.Timedelta(minutes=mins * int(st["expiry_candles"]))
        pos = {"mode": mode, "category": "spot", "symbol": s, "base": base, "interval": st["interval"], "strategy": o["best"],
               "score": o["score"], "hits": o["hits"], "opened_at": pd.Timestamp.now(tz="UTC").isoformat(),
               "close_at": close_at.isoformat(), "simulated": dry}
        if dry:
            q = float(info["order_qty"])
            R.log_order({"mode": mode, "source": "autotrader_dryrun", "category": "spot", "symbol": s, "side": "Buy",
                         "qty": info["order_qty"], "notional": info["order_usdt"], "status": "SIMULADA"})
            pos.update({"orderId": f"dry-{int(time.time() * 1000)}", "qty": q, "net_qty": q * (1 - fee / 100),
                        "entry_price": info["price"], "cost_usdt": info["order_usdt"], "fees": {base: q * fee / 100}})
            self.rt["open"].append(pos)
            return True, "simulada (dry-run)"
        ok, msg, res = R.guarded_order(client, mode=mode, real_armed=bool(st["real_armed"]),
                                       risk={"max_stake_pct": st["max_stake_pct"]}, source="autotrader", category="spot",
                                       symbol=s, side="Buy", qty=info["order_qty"], stake_usdt=info["margin"])
        if not ok:
            return False, msg
        fills = {}
        for _ in range(5):
            time.sleep(0.8)
            try:
                fills = client.spot_fills(s, res.get("orderId"))
                if fills.get("qty"):
                    break
            except Exception:
                pass
        qty = fills.get("qty") or float(info["order_qty"])
        fees = fills.get("fees") or {base: qty * fee / 100}
        pos.update({"orderId": res.get("orderId"), "qty": qty, "net_qty": max(0.0, qty - fees.get(base, 0.0)),
                    "entry_price": fills.get("avg") or info["price"],
                    "cost_usdt": (fills.get("value") or float(info["order_usdt"])) + fees.get("USDT", 0.0), "fees": fees})
        self.rt["open"].append(pos)
        return True, msg

    def kill_actions(self, client, mode, st):
        if st["enabled"]:
            A.update_state(enabled=False)
            self.ev("KILL SWITCH: autônomo desligado", logging.WARNING)
        if not self.rt.get("kill_done") and client.authenticated:
            try:
                client.cancel_all("spot")
                R.log_cancel(mode, "autotrader_kill", "cancel_all spot", True, "")
            except Exception as e:
                R.log_cancel(mode, "autotrader_kill", "cancel_all spot", False, str(e))
            if st["kill_close_all"]:
                for tr in self.rt["open"]:
                    if tr["mode"] == mode:
                        tr["exit_now"] = "kill switch"
                self.manage_exits(client, mode, st)
            self.rt["kill_done"] = True

    def safe_usdt(self, client):
        if not client.authenticated:
            return None
        try:
            free = client.coin_free("USDT")
            w = client.wallet()
            self.rt["wallet"] = {"usdt_free": round(free, 4), "available": round(free, 4), "equity": w["total_equity"],
                                 "account_type": w["account_type"]}
            return free
        except Exception as e:
            self.ev(f"Saldo indisponível: {e}", logging.WARNING)
            return None

    def day_stats(self):
        today = pd.Timestamp.now(tz="America/Sao_Paulo").normalize()
        done = [d for d in self.rt["done"] if not d.get("simulated") and d.get("closed_at")
                and pd.Timestamp(d["closed_at"]).tz_convert("America/Sao_Paulo") >= today]
        self.rt["pnl_today"] = round(sum(d["pnl"] or 0 for d in done), 6)
        self.rt["trades_today"] = len([t for t in self.rt["entries"] if pd.Timestamp(t, unit="s", tz="UTC") >= today])
        self.rt["closed_today"] = len(done)

    # ---------------- loop
    def run(self):
        if self.dry_cli:
            st = A.load_state()
            client, mode = self.get_client(st)
            self.refresh_universe(client, st, self.safe_usdt(client))
            for s in self.universe(st):
                if self.rt["symbols"].get(s, {}).get("eligible"):
                    self.train(s, st)
        else:
            threading.Thread(target=self.trainer_loop, daemon=True).start()
        loops, last_uni, usdt = 0, 0.0, None
        while not self.stop:
            st = A.load_state()
            client, mode = self.get_client(st)
            try:
                if A.STOP_FLAG.exists() and not self.dry_cli:
                    self.ev("STOP_WORKER encontrado: encerrando")
                    break
                if R.kill_active():
                    self.kill_actions(client, mode, st)
                else:
                    self.rt["kill_done"] = False
                if time.time() - last_uni > 20:
                    usdt = self.safe_usdt(client)
                    self.refresh_universe(client, st, usdt)
                    last_uni = time.time()
                self.day_stats()
                self.manage_exits(client, mode, st)
                self.scan_and_enter(client, mode, st, usdt, force_eval=self.dry_cli)
            except Exception as e:
                self.ev(f"Erro no loop: {e}", logging.ERROR)
                time.sleep(5)
            self.save_rt(st, mode)
            loops += 1
            if self.max_loops and loops >= self.max_loops:
                break
            time.sleep(max(1, int(st["loop_seconds"])))
        self.stop = True
        self.save_rt(A.load_state(), self.client_key or "")


def detached(args):
    py = ROOT / ".venv" / "Scripts" / ("pythonw.exe" if os.name == "nt" else "python")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([str(py), *args], cwd=str(ROOT), creationflags=flags, close_fds=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--loops", type=int, default=0)
    ap.add_argument("--trials", type=int, default=None)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.status:
        rt = A.load_runtime()
        print(f"heartbeat há {A.heartbeat_age():.0f}s | pid {rt.get('pid')} vivo={A.pid_alive(rt.get('pid'))} | "
              f"env {rt.get('env')} | ligado {rt.get('enabled')} | armado {rt.get('armed')} | parado={A.STOP_FLAG.exists()}")
        return
    if a.stop:
        A.STOP_FLAG.write_text("stop", encoding="utf-8")
        rt = A.load_runtime()
        for _ in range(20):
            if not A.pid_alive(rt.get("pid")):
                break
            time.sleep(0.5)
        if A.pid_alive(rt.get("pid")):
            subprocess.run(["taskkill", "/PID", str(rt["pid"]), "/F"], capture_output=True)
        print("Autotrader parado (watchdog não reinicia até iniciar_autotrader.bat).")
        return
    if a.start:
        A.STOP_FLAG.unlink(missing_ok=True)
        rt = A.load_runtime()
        if A.pid_alive(rt.get("pid")) and A.heartbeat_age() < 60:
            print("Autotrader já está rodando.")
        else:
            detached(["autotrader.py"])
            print("Autotrader iniciado em segundo plano.")
        return
    setup_log(console=a.dry_run)
    if not a.dry_run:
        lock = socket.socket()
        try:
            lock.bind(("127.0.0.1", LOCK_PORT))
        except OSError:
            LOG.info("Outra instância já está rodando — saindo.")
            return
        if A.STOP_FLAG.exists():
            LOG.info("STOP_WORKER presente — não inicia.")
            return
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()] or None
    LOG.info(f"Autotrader iniciado (dry_run={a.dry_run}, pid={os.getpid()})")
    Worker(dry_cli=a.dry_run, loops=a.loops, symbols=syms, trials=a.trials).run()
    LOG.info("Autotrader finalizado")


if __name__ == "__main__":
    main()
