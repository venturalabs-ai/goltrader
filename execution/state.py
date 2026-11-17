"""Estado persistido em SQLite (WAL) no diretório de estado (GOLTRADER_STATE_DIR).

Guarda: ordens (com orderLinkId, gravadas ANTES do envio), execuções, posições, carteira, eventos de auditoria,
chave/valor (flags, equity do início do dia) e fila de comandos (API/CLI → worker). Sobrevive a reinícios.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

TERMINAL = {"Filled", "Cancelled", "Rejected", "PartiallyFilledCanceled", "Deactivated", "Expired", "LOST", "SEND_REJECTED"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(
  order_link_id TEXT PRIMARY KEY, order_id TEXT, category TEXT, symbol TEXT, side TEXT, order_type TEXT,
  qty REAL, price REAL, reduce_only INTEGER DEFAULT 0, status TEXT, cum_exec_qty REAL DEFAULT 0, avg_price REAL,
  external INTEGER DEFAULT 0, created_ms INTEGER, updated_ms INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS executions(
  exec_id TEXT PRIMARY KEY, order_link_id TEXT, category TEXT, symbol TEXT, side TEXT, qty REAL, price REAL,
  fee REAL, pnl REAL, ts_ms INTEGER);
CREATE TABLE IF NOT EXISTS positions(
  category TEXT, symbol TEXT, position_idx INTEGER DEFAULT 0, side TEXT, size REAL, avg_price REAL, unrealised REAL,
  updated_ms INTEGER, PRIMARY KEY(category, symbol, position_idx));
CREATE TABLE IF NOT EXISTS wallet(coin TEXT PRIMARY KEY, equity REAL, available REAL, updated_ms INTEGER);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts_ms INTEGER, kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS commands(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts_ms INTEGER, kind TEXT, payload TEXT, status TEXT DEFAULT 'pending', result TEXT);
"""


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class StateStore:
    def __init__(self, path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)

    def now_ms(self) -> int:
        return int(self._clock() * 1000)

    def close(self) -> None:
        self.db.close()

    def _run(self, sql: str, args: tuple = ()):
        with self._lock:
            return self.db.execute(sql, args)

    # ---- kv / eventos ----
    def kv_get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        r = self._run("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def kv_set(self, key: str, value: str) -> None:
        self._run("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def kv_del(self, key: str) -> None:
        self._run("DELETE FROM kv WHERE key=?", (key,))

    def event(self, kind: str, detail: Any = "") -> None:
        self._run("INSERT INTO events(ts_ms,kind,detail) VALUES(?,?,?)",
                  (self.now_ms(), kind, detail if isinstance(detail, str) else json.dumps(detail, default=str)))

    def events(self, kind: Optional[str] = None, limit: int = 100) -> List[dict]:
        q, a = "SELECT * FROM events", ()
        if kind:
            q, a = q + " WHERE kind=?", (kind,)
        return [dict(r) for r in self._run(q + " ORDER BY id DESC LIMIT ?", a + (limit,)).fetchall()]

    # ---- ordens ----
    def insert_order(self, link: str, category: str, symbol: str, side: str, order_type: str, qty: float,
                     price: Optional[float], reduce_only: bool, status: str = "PENDING") -> bool:
        """False se o orderLinkId já existe (idempotência)."""
        now = self.now_ms()
        with self._lock:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO orders(order_link_id,category,symbol,side,order_type,qty,price,reduce_only,status,created_ms,updated_ms)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (link, category, symbol, side, order_type, qty, price, int(reduce_only), status, now, now))
            return cur.rowcount == 1

    def get_order(self, link: str) -> Optional[dict]:
        r = self._run("SELECT * FROM orders WHERE order_link_id=?", (link,)).fetchone()
        return dict(r) if r else None

    def update_order(self, link: str, **fields) -> None:
        if not fields:
            return
        fields["updated_ms"] = self.now_ms()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._run(f"UPDATE orders SET {cols} WHERE order_link_id=?", (*fields.values(), link))

    def upsert_exchange_order(self, o: Dict[str, Any], category: str) -> str:
        """Atualiza (ou cria, marcando external=1) uma ordem a partir de um dict da Bybit. Retorna a chave."""
        link = o.get("orderLinkId") or f"ext-{o.get('orderId')}"
        qty, price = _f(o.get("qty")), _f(o.get("price")) or None
        self._run("INSERT OR IGNORE INTO orders(order_link_id,category,symbol,side,order_type,qty,price,reduce_only,status,external,created_ms,updated_ms)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (link, category, o.get("symbol"), o.get("side"), o.get("orderType"), qty, price,
                   int(bool(o.get("reduceOnly"))), o.get("orderStatus", "New"), 0 if o.get("orderLinkId") else 1,
                   self.now_ms(), self.now_ms()))
        self.update_order(link, order_id=o.get("orderId"), status=o.get("orderStatus"), cum_exec_qty=_f(o.get("cumExecQty")),
                          avg_price=_f(o.get("avgPrice")) or None)
        return link

    def active_orders(self) -> List[dict]:
        ph = ",".join("?" * len(TERMINAL))
        return [dict(r) for r in self._run(f"SELECT * FROM orders WHERE status NOT IN ({ph})", tuple(TERMINAL)).fetchall()]

    # ---- execuções ----
    def add_execution(self, e: Dict[str, Any], category: str) -> bool:
        with self._lock:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO executions(exec_id,order_link_id,category,symbol,side,qty,price,fee,pnl,ts_ms) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (e.get("execId"), e.get("orderLinkId"), category, e.get("symbol"), e.get("side"), _f(e.get("execQty")),
                 _f(e.get("execPrice")), _f(e.get("execFee")), _f(e.get("execPnl")), int(_f(e.get("execTime"), self.now_ms()))))
            return cur.rowcount == 1

    def last_execution_ms(self) -> int:
        r = self._run("SELECT MAX(ts_ms) m FROM executions").fetchone()
        return int(r["m"] or 0)

    # ---- posições / carteira ----
    def replace_positions(self, category: str, rows: List[Dict[str, Any]]) -> None:
        with self._lock:
            self.db.execute("DELETE FROM positions WHERE category=?", (category,))
            for p in rows:
                self._put_position(category, p)

    def _put_position(self, category: str, p: Dict[str, Any]) -> None:
        size = _f(p.get("size"))
        idx = int(_f(p.get("positionIdx")))
        if size <= 0:
            self.db.execute("DELETE FROM positions WHERE category=? AND symbol=? AND position_idx=?", (category, p.get("symbol"), idx))
            return
        self.db.execute("INSERT OR REPLACE INTO positions(category,symbol,position_idx,side,size,avg_price,unrealised,updated_ms) VALUES(?,?,?,?,?,?,?,?)",
                        (category, p.get("symbol"), idx, p.get("side"), size, _f(p.get("avgPrice") or p.get("entryPrice")),
                         _f(p.get("unrealisedPnl")), self.now_ms()))

    def upsert_position(self, category: str, p: Dict[str, Any]) -> None:
        with self._lock:
            self._put_position(category, p)

    def positions(self, category: Optional[str] = None) -> List[dict]:
        q, a = "SELECT * FROM positions", ()
        if category:
            q, a = q + " WHERE category=?", (category,)
        return [dict(r) for r in self._run(q, a).fetchall()]

    def signed_position_qty(self, symbol: str) -> float:
        tot = 0.0
        for r in self._run("SELECT side,size FROM positions WHERE symbol=?", (symbol,)).fetchall():
            tot += r["size"] if r["side"] == "Buy" else -r["size"]
        return tot

    def set_wallet(self, coins: List[Dict[str, Any]], total_equity: Optional[float] = None) -> None:
        with self._lock:
            for c in coins:
                self.db.execute("INSERT OR REPLACE INTO wallet(coin,equity,available,updated_ms) VALUES(?,?,?,?)",
                                (c.get("coin"), _f(c.get("equity") or c.get("walletBalance")),
                                 _f(c.get("availableToWithdraw") or c.get("free")), self.now_ms()))
        if total_equity is not None:
            self.kv_set("total_equity", str(total_equity))

    def wallet_coin(self, coin: str) -> float:
        r = self._run("SELECT equity FROM wallet WHERE coin=?", (coin,)).fetchone()
        return float(r["equity"]) if r else 0.0

    # ---- fila de comandos ----
    def enqueue(self, kind: str, payload: Optional[dict] = None) -> int:
        cur = self._run("INSERT INTO commands(ts_ms,kind,payload) VALUES(?,?,?)", (self.now_ms(), kind, json.dumps(payload or {})))
        return int(cur.lastrowid)

    def next_command(self) -> Optional[dict]:
        r = self._run("SELECT * FROM commands WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
        return dict(r) if r else None

    def finish_command(self, cmd_id: int, status: str, result: Any) -> None:
        self._run("UPDATE commands SET status=?, result=? WHERE id=?", (status, json.dumps(result, default=str), cmd_id))

    def command(self, cmd_id: int) -> Optional[dict]:
        r = self._run("SELECT * FROM commands WHERE id=?", (cmd_id,)).fetchone()
        return dict(r) if r else None
