"""Motor de execução Bybit: ordens idempotentes, limites de risco, reconciliação, kill switch.

Fluxo de uma ordem (`submit`):
  kill switch/halt → idempotência (orderLinkId já visto?) → risco → grava PENDING no SQLite → envia → atualiza.
Se o envio falhar por rede (resultado desconhecido) o registro fica `UNKNOWN`; `reconcile()` resolve pelo orderLinkId.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from brokers.base import KillSwitchActive, Mode, OrderRequest, OrderResult
from brokers.bybit import BybitAdapter, new_link_id
from .risk import RiskGuard, RiskRejected
from .state import StateStore

log = logging.getLogger("goltrader.exec")
QUOTES = ("USDT", "USDC")


def base_coin(symbol: str) -> str:
    for q in QUOTES:
        if symbol.endswith(q):
            return symbol[: -len(q)]
    return symbol


class ExecutionEngine:
    def __init__(self, adapters: Dict[str, BybitAdapter], store: StateStore, risk: RiskGuard, mode: Mode,
                 state_dir, clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.adapters, self.store, self.risk, self.mode = adapters, store, risk, mode
        self.kill_flag = Path(state_dir) / "KILL_SWITCH"
        self._clock, self._sleep = clock, sleep
        self.last_reconcile_ms = 0

    # ---------------- utilidades ----------------
    def today(self) -> str:
        return dt.datetime.fromtimestamp(self._clock(), dt.timezone.utc).strftime("%Y-%m-%d")

    @property
    def has_credentials(self) -> bool:
        return any(a.rest.has_credentials for a in self.adapters.values())

    def kill_active(self) -> bool:
        from brokers.base import kill_active
        return self.kill_flag.exists() or kill_active()

    def halted(self) -> Optional[str]:
        return self.store.kv_get("halt_reason") if self.store.kv_get("halt_day") == self.today() else None

    def daily_loss(self) -> float:
        return float(self.store.kv_get("daily_loss", "0") or 0)

    def signed_qty(self, category: str, symbol: str) -> float:
        if category == "spot":
            return self.store.wallet_coin(base_coin(symbol))
        return self.store.signed_position_qty(symbol)

    # ---------------- ordens ----------------
    def submit(self, req: OrderRequest, category: str = "linear") -> OrderResult:
        adapter = self.adapters.get(category)
        if adapter is None:
            raise ValueError(f"categoria sem adapter: {category}")
        if self.kill_active():
            raise KillSwitchActive("Kill switch ativo: ordens bloqueadas.")
        if not req.reduce_only and self.halted():
            raise RiskRejected("halt", f"operação travada hoje: {self.halted()}")
        link = req.client_id or new_link_id()
        req.client_id = link
        prev = self.store.get_order(link)
        if prev is not None:   # idempotência: mesma ordem não é reenviada
            return OrderResult(prev["status"] not in ("SEND_REJECTED", "LOST"), prev.get("order_id"), prev["status"],
                               self.mode, {"idempotent": True})
        price = req.price or adapter.last_price(req.symbol)
        self.risk.check(req, price, self.signed_qty(category, req.symbol), self.daily_loss())
        self.store.insert_order(link, category, req.symbol.upper(), req.side.capitalize(), req.order_type, req.qty,
                                req.price, req.reduce_only)
        self.risk.record_order()
        try:
            res = adapter.place_order(req)
        except KillSwitchActive:
            self.store.update_order(link, status="SEND_REJECTED", detail="kill switch")
            raise
        except Exception as e:  # noqa: BLE001  resultado desconhecido → reconcile resolve
            self.store.update_order(link, status="UNKNOWN", detail=f"{type(e).__name__}: {e}")
            self.store.event("order_unknown", {"link": link, "error": str(e)})
            raise
        if res.ok:
            self.store.update_order(link, order_id=res.order_id, status="PAPER" if self.mode is Mode.PAPER else (res.status or "New"))
        else:
            self.store.update_order(link, status="SEND_REJECTED", detail=res.status)
            self.store.event("order_rejected", {"link": link, "reason": res.status})
        return res

    # ---------------- reconciliação ----------------
    def reconcile(self) -> Dict[str, Any]:
        if not self.has_credentials:
            return {"skipped": "sem_credenciais"}
        summary: Dict[str, Any] = {"updated": 0, "lost": 0, "positions": 0, "executions": 0, "equity": None}
        start_of_day = int(dt.datetime.strptime(self.today(), "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        for cat, ad in self.adapters.items():
            seen = set()
            for o in ad.open_orders():
                seen.add(self.store.upsert_exchange_order(o, cat))
                summary["updated"] += 1
            for local in self.store.active_orders():
                if local["category"] != cat or local["order_link_id"] in seen:
                    continue
                found = ad.get_order(local["order_link_id"], local["symbol"])
                if found:
                    self.store.upsert_exchange_order(found, cat)
                    summary["updated"] += 1
                else:
                    self.store.update_order(local["order_link_id"], status="LOST", detail="não encontrada na corretora")
                    self.store.event("reconcile_lost", local["order_link_id"])
                    summary["lost"] += 1
            if cat == "linear":
                rows = ad.positions()
                self.store.replace_positions(cat, rows)
                summary["positions"] = len(rows)
            for e in ad.executions(start_ms=max(self.store.last_execution_ms(), start_of_day)):
                summary["executions"] += int(self.store.add_execution(e, cat))
        eq = self._parse_balance(next(iter(self.adapters.values())).balance())
        if eq is not None:
            summary["equity"] = eq
            self.update_equity(eq)
        self.last_reconcile_ms = self.store.now_ms()
        self.store.kv_set("last_reconcile_ms", str(self.last_reconcile_ms))
        self.store.event("reconcile", summary)
        return summary

    def _parse_balance(self, j: Dict[str, Any]) -> Optional[float]:
        lst = (j.get("result") or {}).get("list") or []
        if not lst:
            return None
        acc = lst[0]
        self.store.set_wallet(acc.get("coin") or [], None)
        try:
            return float(acc.get("totalEquity"))
        except (TypeError, ValueError):
            return None

    # ---------------- perda diária ----------------
    def update_equity(self, equity: float) -> bool:
        """Registra equity; retorna True se o limite de perda diária foi estourado (e agiu)."""
        self.store.kv_set("total_equity", str(equity))
        if self.store.kv_get("equity_day") != self.today():
            self.store.kv_set("equity_day", self.today())
            self.store.kv_set("equity_start", str(equity))
        loss = max(0.0, float(self.store.kv_get("equity_start", str(equity))) - equity)
        self.store.kv_set("daily_loss", f"{loss:.4f}")
        lim = self.risk.limits
        if loss >= lim.max_daily_loss_usd and not self.halted():
            self.store.kv_set("halt_day", self.today())
            self.store.kv_set("halt_reason", f"perda diária {loss:.2f} ≥ {lim.max_daily_loss_usd:.2f} USDT")
            self.store.event("halt_perda_diaria", {"loss": loss, "limit": lim.max_daily_loss_usd})
            log.error("PERDA DIÁRIA estourada (%.2f): operação travada", loss)
            if lim.kill_on_daily_loss:
                self.kill_switch(f"perda diária {loss:.2f}")
            return True
        return False

    # ---------------- WebSocket ----------------
    def handle_ws(self, topic: str, items: List[Dict[str, Any]]) -> None:
        for it in items:
            cat = it.get("category") or "linear"
            if topic == "order":
                self.store.upsert_exchange_order(it, cat)
            elif topic == "execution":
                self.store.add_execution(it, cat)
            elif topic == "position":
                self.store.upsert_position(cat, it)
            elif topic == "wallet":
                te = it.get("totalEquity")
                self.store.set_wallet(it.get("coin") or [], None)
                if te not in (None, ""):
                    self.update_equity(float(te))

    # ---------------- kill switch ----------------
    def kill_switch(self, reason: str = "manual", create_flag: bool = True) -> Dict[str, Any]:
        if create_flag:
            self.kill_flag.parent.mkdir(parents=True, exist_ok=True)
            self.kill_flag.write_text(f"{reason}\n{dt.datetime.now(dt.timezone.utc).isoformat()}\n")
        self.store.event("kill_switch", reason)
        report: Dict[str, Any] = {"reason": reason, "adapters": {}, "remaining": []}
        if self.mode is Mode.PAPER:
            self.store.kv_set("kill_executed_ms", str(self.store.now_ms()))
            return report
        for attempt in range(3):
            for cat, ad in self.adapters.items():
                try:
                    report["adapters"][cat] = ad.close_all()
                except Exception as e:  # noqa: BLE001  não pode abortar o loop de fechamento
                    report["adapters"][cat] = {"error": str(e)}
                    log.exception("kill switch: close_all(%s) falhou", cat)
            report["remaining"] = self._remaining_positions()
            if not report["remaining"]:
                break
            self._sleep(1.0)
        self.store.kv_set("kill_executed_ms", str(self.store.now_ms()))
        self.store.event("kill_switch_done", {"remaining": report["remaining"]})
        return report

    def _remaining_positions(self) -> List[Dict[str, Any]]:
        out = []
        for cat, ad in self.adapters.items():
            if cat != "linear":
                continue
            try:
                out += [{"symbol": p["symbol"], "size": p["size"]} for p in ad.positions()]
            except Exception as e:  # noqa: BLE001
                out.append({"error": str(e)})
        return out

    def kill_pending(self) -> bool:
        """Flag existe mas o fechamento ainda não foi executado (flag criada por fora: CLI/API/arquivo)."""
        return self.kill_flag.exists() and not self.store.kv_get("kill_executed_ms")

    def clear_kill(self) -> None:
        if self.kill_flag.exists():
            self.kill_flag.unlink()
        self.store.kv_del("kill_executed_ms")
        self.store.event("kill_switch_cleared", "")

    def status(self) -> Dict[str, Any]:
        return {"mode": self.mode.value, "kill": self.kill_active(), "halted": self.halted(),
                "daily_loss": self.daily_loss(), "equity": self.store.kv_get("total_equity"),
                "positions": len(self.store.positions()), "active_orders": len(self.store.active_orders()),
                "last_reconcile_ms": self.store.kv_get("last_reconcile_ms"), "has_credentials": self.has_credentials}
