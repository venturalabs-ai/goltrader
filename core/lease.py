"""Lease (trava com validade) em SQLite: garante UMA instância operando por diretório de estado.

Uso: lease = Lease(state_dir/'lease.db', owner='host:pid'); lease.acquire(); loop: lease.renew(); lease.release().
Se `renew()` falhar (outra instância assumiu), quem opera deve parar de enviar ordens.
Limite: vale para instâncias que compartilham o MESMO arquivo (mesmo volume). Notebook e VPS não compartilham
disco: ver docs/DEPLOY-VPS.md (regra operacional: LIVE só em um lugar).
"""
from __future__ import annotations

import os
import socket
import sqlite3
import time
from pathlib import Path
from typing import Callable, Optional


def default_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


class LeaseHeld(RuntimeError):
    pass


class Lease:
    def __init__(self, path, owner: Optional[str] = None, ttl: float = 30.0, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.owner = owner or default_owner()
        self.ttl, self._clock = float(ttl), clock
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS lease(id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT, expires REAL)")

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        return c

    def _take(self, allow_new: bool) -> bool:
        now = self._clock()
        c = self._conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT owner, expires FROM lease WHERE id=1").fetchone()
            free = row is None or row[1] <= now
            mine = row is not None and row[0] == self.owner
            if mine or (free and allow_new):
                c.execute("INSERT INTO lease(id, owner, expires) VALUES(1,?,?) "
                          "ON CONFLICT(id) DO UPDATE SET owner=excluded.owner, expires=excluded.expires",
                          (self.owner, now + self.ttl))
                c.execute("COMMIT")
                return True
            c.execute("ROLLBACK")
            return False
        finally:
            c.close()

    def acquire(self) -> bool:
        return self._take(allow_new=True)

    def renew(self) -> bool:
        """Só renova se ainda for o dono (não reconquista lease expirada tomada por outro)."""
        return self._take(allow_new=False)

    def holder(self) -> Optional[str]:
        c = self._conn()
        try:
            row = c.execute("SELECT owner, expires FROM lease WHERE id=1").fetchone()
        finally:
            c.close()
        if row and row[1] > self._clock():
            return row[0]
        return None

    def release(self) -> None:
        c = self._conn()
        try:
            c.execute("DELETE FROM lease WHERE id=1 AND owner=?", (self.owner,))
        finally:
            c.close()
