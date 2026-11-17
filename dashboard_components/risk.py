"""Limites de risco, kill switch e log de ordens (sem chaves)."""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dashboard_components import keys

ROOT = keys.ROOT
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
ORDERS_LOG = LOG_DIR / "orders.log"
KILL_FLAG = LOG_DIR / "KILL_SWITCH"
RISK_FILE = LOG_DIR / "risk_config.json"
AUTO_FILE = LOG_DIR / "auto_state.json"
TOURNAMENT_FILE = LOG_DIR / "last_tournament.json"
_lock = threading.Lock()

DEFAULT_RISK = {"max_stake_pct": 0.0, "max_daily_loss_usdt": 0.0, "max_open_positions": 0}  # 0 = sem limite


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data):
    with _lock:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def load_risk() -> Dict[str, float]:
    return {**DEFAULT_RISK, **load_json(RISK_FILE, {})}


def save_risk(cfg: Dict[str, float]):
    save_json(RISK_FILE, cfg)


def kill_active() -> bool:
    return KILL_FLAG.exists()


def set_kill(active: bool, reason: str = ""):
    if active:
        KILL_FLAG.write_text(f"{datetime.now(timezone.utc).isoformat()} {reason}", encoding="utf-8")
    elif KILL_FLAG.exists():
        KILL_FLAG.unlink()


def log_order(event: Dict[str, Any]):
    rec = {"ts_utc": datetime.now(timezone.utc).isoformat(), **event}
    line = keys.scrub(json.dumps(rec, ensure_ascii=False, default=str))
    with _lock, ORDERS_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_orders_log(n: int = 200):
    if not ORDERS_LOG.exists():
        return []
    lines = ORDERS_LOG.read_text(encoding="utf-8").splitlines()[-n:]
    out = []
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def pre_trade_check(client, *, mode: str, real_armed: bool, category: str, stake_usdt: float,
                    reduce_only: bool, risk: Dict[str, float]) -> Tuple[bool, str, Dict[str, Any]]:
    """Valida TODAS as regras antes de qualquer ordem. Falha fechada (bloqueia) se nao conseguir verificar."""
    info: Dict[str, Any] = {}
    if not client.authenticated:
        return False, ("chaves da conta real não configuradas" if mode == keys.REAL else "chaves da testnet não configuradas"), info
    if mode == keys.REAL and not real_armed:
        return False, "Conta REAL não armada (ative e digite CONFIRMAR)", info
    if reduce_only:  # fechar/reduzir posicao e sempre permitido (inclusive com kill switch)
        return True, "ok (ordem de redução/fechamento)", info
    if kill_active():
        return False, "KILL SWITCH ativo — rearme no painel para operar", info
    try:
        w = client.wallet()
        info["available"] = w["available"]
        if stake_usdt > w["available"]:
            return False, f"Saldo insuficiente: margem {stake_usdt:.2f} USDT > disponível {w['available']:.2f} USDT", info
        pct = 100.0 * stake_usdt / w["available"] if w["available"] > 0 else 999.0
        info["stake_pct"] = round(pct, 3)
        if float(risk.get("max_stake_pct") or 0) > 0 and pct > float(risk["max_stake_pct"]):
            return False, f"Stake {pct:.2f}% do saldo disponível excede o limite de {risk['max_stake_pct']}%", info
        if float(risk.get("max_daily_loss_usdt") or 0) > 0:
            pnl = client.closed_pnl_today()
            info["pnl_hoje"] = round(pnl, 4)
        if float(risk.get("max_daily_loss_usdt") or 0) > 0 and pnl <= -abs(float(risk["max_daily_loss_usdt"])):
            return False, f"Perda diária {pnl:.2f} USDT atingiu o limite de {risk['max_daily_loss_usdt']} USDT", info
        if category == "linear" and int(risk.get("max_open_positions") or 0) > 0:
            n = len(client.positions("linear"))
            info["posicoes_abertas"] = n
            if n >= int(risk["max_open_positions"]):
                return False, f"{n} posições abertas — limite é {int(risk['max_open_positions'])}", info
    except Exception as e:
        return False, f"Não foi possível verificar o risco (bloqueado por segurança): {keys.scrub(e)}", info
    return True, "ok", info


def guarded_order(client, *, mode: str, real_armed: bool, risk: Dict[str, float], source: str,
                  category: str, symbol: str, side: str, qty: str, order_type: str = "Market",
                  price: Optional[str] = None, take_profit: Optional[str] = None, stop_loss: Optional[str] = None,
                  reduce_only: bool = False, stake_usdt: float = 0.0) -> Tuple[bool, str, Dict[str, Any]]:
    """Ponto UNICO de envio de ordem: checa risco, envia, loga."""
    base = {"mode": mode, "source": source, "category": category, "symbol": symbol, "side": side,
            "orderType": order_type, "qty": qty, "price": price, "takeProfit": take_profit,
            "stopLoss": stop_loss, "reduceOnly": reduce_only, "stake_usdt": round(float(stake_usdt), 4)}
    ok, reason, info = pre_trade_check(client, mode=mode, real_armed=real_armed, category=category,
                                       stake_usdt=stake_usdt, reduce_only=reduce_only, risk=risk)
    if not ok:
        log_order({**base, "status": "BLOQUEADA", "reason": reason, "risk": info})
        return False, reason, {}
    try:
        res = client.place_order(category, symbol, side, qty, order_type, price, take_profit, stop_loss, reduce_only)
        log_order({**base, "status": "ENVIADA", "orderId": res.get("orderId"), "risk": info})
        return True, f"Ordem enviada: {res.get('orderId')}", res
    except Exception as e:
        msg = keys.scrub(e)
        log_order({**base, "status": "ERRO", "reason": msg, "risk": info})
        return False, msg, {}


def log_cancel(mode: str, source: str, what: str, ok: bool, detail: str):
    log_order({"mode": mode, "source": source, "action": what, "status": "OK" if ok else "ERRO", "detail": keys.scrub(detail)})
