"""CLI operacional: status | kill | clear-kill | reconcile | submit.

  python -m execution.cli status
  python -m execution.cli kill "motivo"          # cria KILL_SWITCH (bloqueia ordens) e enfileira fechamento reduce-only
  python -m execution.cli clear-kill --confirm   # remove a trava (exige --confirm)
  python -m execution.cli reconcile
"""
from __future__ import annotations

import argparse
import json
import sys

from core.safety import state_dir
from .state import StateStore


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="goltrader-exec")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    k = sub.add_parser("kill")
    k.add_argument("reason", nargs="?", default="cli")
    c = sub.add_parser("clear-kill")
    c.add_argument("--confirm", action="store_true")
    sub.add_parser("reconcile")
    s = sub.add_parser("submit")
    s.add_argument("category", choices=["spot", "linear"])
    s.add_argument("symbol")
    s.add_argument("side", choices=["buy", "sell"])
    s.add_argument("qty", type=float)
    s.add_argument("--limit", type=float)
    s.add_argument("--reduce-only", action="store_true")
    s.add_argument("--client-id")
    a = ap.parse_args(argv)

    sdir = state_dir()
    store = StateStore(sdir / "goltrader.db")
    flag = sdir / "KILL_SWITCH"
    if a.cmd == "status":
        hb = sdir / "heartbeat.json"
        print(hb.read_text() if hb.exists() else json.dumps({"error": "sem heartbeat (worker parado?)"}))
        print("kill_switch:", flag.exists())
    elif a.cmd == "kill":
        flag.write_text(a.reason + "\n")
        store.kv_del("kill_executed_ms")
        store.enqueue("kill", {"reason": a.reason})
        print("KILL_SWITCH criado; o worker fecha as posições (reduce-only).")
    elif a.cmd == "clear-kill":
        if not a.confirm:
            print("Use --confirm para remover o kill switch.")
            return 2
        flag.unlink(missing_ok=True)
        store.kv_del("kill_executed_ms")
        store.event("kill_switch_cleared", "cli")
        print("Kill switch removido.")
    elif a.cmd == "reconcile":
        print("comando", store.enqueue("reconcile"))
    elif a.cmd == "submit":
        print("comando", store.enqueue("submit", {"category": a.category, "symbol": a.symbol, "side": a.side, "qty": a.qty,
                                                   "order_type": "limit" if a.limit else "market", "price": a.limit,
                                                   "reduce_only": a.reduce_only, "client_id": a.client_id}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
