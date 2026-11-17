"""Estado compartilhado entre o painel e o worker 24/7 (autotrader.py).

Limites: 0 = SEM LIMITE (padrao). So existe o tamanho minimo + 0,15 USDT,
kill switch, armar conta real com CONFIRMAR e parada por saldo insuficiente.
"""
import json
import os
import time
from pathlib import Path
from typing import Any, Dict

from dashboard_components import keys

ROOT = keys.ROOT
STATE_DIR = ROOT / "state"
STATE_DIR.mkdir(exist_ok=True)
STATE_FILE = STATE_DIR / "autotrader.json"
RUNTIME_FILE = STATE_DIR / "autotrader_runtime.json"
CHAMPS_FILE = STATE_DIR / "champions.json"
STOP_FLAG = STATE_DIR / "STOP_WORKER"

DEFAULT_UNIVERSE = ["DOGEUSDT", "XRPUSDT", "ADAUSDT", "TRXUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT",
                    "LTCUSDT", "BNBUSDT", "ETHUSDT", "BTCUSDT"]

DEFAULTS: Dict[str, Any] = {
    "enabled": False,            # liga/desliga as entradas automaticas
    "env": "real",               # real | testnet
    "real_armed": False,         # armado no painel com CONFIRMAR (persistente)
    "armed_at": None,
    "dry_run": False,            # True = simula ordens (nao envia)
    "universe": DEFAULT_UNIVERSE,
    "interval": "1",
    "expiry_candles": 1,
    "category": "spot",          # Bybit SPOT (perpetuo bloqueado no Brasil, ErrCode 10024)
    "extra_usdt": 0.15,          # valor minimo da Bybit + 0,15 USDT
    "fee_pct": 0.1,              # taxa spot por lado (%) — entra no calculo do edge
    "min_net_edge_pct": 0.0,     # edge medio liquido de taxas (%) minimo na OOS
    "min_long_trades": 5,        # minimo de compras na janela OOS
    "leverage": 1,
    # ---- limites opcionais: 0 = sem limite ----
    "max_open_positions": 0,
    "max_daily_loss_usdt": 0.0,
    "max_trades_per_hour": 0,
    "cooldown_min": 0.0,
    "max_stake_pct": 0.0,
    "tp_pct": 0.0,
    "sl_pct": 0.0,
    # ---- selecao ----
    "require_bonferroni": False,
    "allow_top_ev_when_none_approved": True,
    "min_ev": 0.0,
    "min_trades_oos": 10,
    "score_min": 0.0,            # score minimo (EV OOS validado x forca da oportunidade)
    "use_orderbook": True,
    "retrain_every_min": 60,
    "n_trials": 8,
    "kill_close_all": False,
    "loop_seconds": 3,
}


def _read(path: Path, default):
    for _ in range(5):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except Exception:
            time.sleep(0.1)
    return default


def _write(path: Path, data):
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    for _ in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    tmp.unlink(missing_ok=True)


def load_state() -> Dict[str, Any]:
    return {**DEFAULTS, **_read(STATE_FILE, {})}


def save_state(st: Dict[str, Any]):
    _write(STATE_FILE, st)


def update_state(**kw) -> Dict[str, Any]:
    st = load_state()
    st.update(kw)
    save_state(st)
    return st


def load_runtime(path: Path = None) -> Dict[str, Any]:
    return _read(path or RUNTIME_FILE, {})


def save_runtime(rt: Dict[str, Any], path: Path = None):
    _write(path or RUNTIME_FILE, rt)


def load_champs() -> Dict[str, Any]:
    return _read(CHAMPS_FILE, {})


def save_champs(ch: Dict[str, Any]):
    _write(CHAMPS_FILE, ch)


def heartbeat_age() -> float:
    hb = load_runtime().get("heartbeat")
    return (time.time() - float(hb)) if hb else 1e9


def pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return code.value == 259
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
