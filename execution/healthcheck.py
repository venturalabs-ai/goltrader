"""Healthcheck do worker: exit 0 se o heartbeat é recente. Uso: python -m execution.healthcheck [max_idade_s]"""
from __future__ import annotations

import json
import sys
import time

from core.safety import state_dir


def check(max_age: float = 30.0, now: float = None) -> tuple:
    p = state_dir(create=False) / "heartbeat.json"
    try:
        hb = json.loads(p.read_text())
    except (OSError, ValueError):
        return False, "sem heartbeat"
    age = (now or time.time()) - float(hb.get("ts", 0))
    if age > max_age:
        return False, f"heartbeat velho ({age:.0f}s)"
    if not hb.get("lease_ok"):
        return False, "lease perdido"
    return True, f"ok modo={hb.get('mode')} ws={hb.get('ws_connected')} kill={hb.get('kill')} halt={hb.get('halted')}"


if __name__ == "__main__":
    ok, msg = check(float(sys.argv[1]) if len(sys.argv) > 1 else 30.0)
    print(msg)
    sys.exit(0 if ok else 1)
