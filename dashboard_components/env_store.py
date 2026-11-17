"""Gravacao segura de variaveis no .env + teste read-only de chaves Bybit.

Nunca imprime/loga valores. Backups .env.bak_<timestamp> (mantem os 3 ultimos).
"""
import os
import re
from datetime import datetime
from typing import Any, Dict, List

from pybit.unified_trading import HTTP

from dashboard_components import keys

ENV_PATH = keys.ROOT / ".env"
HINTS = {
    "10003": "API key inválida — confira se copiou a key inteira e se ela é do ambiente certo (chave da testnet só funciona na testnet, e a da conta real só na real).",
    "10004": "Secret não confere com a key (erro de assinatura) — copie o secret de novo, inteiro. A Bybit mostra o secret uma vez só; se perdeu, crie outra chave.",
    "10005": "Permissão negada para esta chave — habilite as permissões de leitura e trade (Unified Trading / Contratos / Spot).",
    "10010": "IP não liberado na whitelist da chave — adicione o IP público deste PC na chave ou crie a chave sem restrição de IP.",
    "33004": "Chave expirada — crie uma nova chave na Bybit.",
}


def hint_for(text: str) -> str:
    for code, h in HINTS.items():
        if code in str(text):
            return h
    return ""


def key_status(mode: str) -> str:
    k, s, src = keys.resolve(mode)
    if k and s:
        return f"configurada (termina em ****{k[-4:]}) — via {src.split(' ')[0]}"
    return "não configurada"


def _backup():
    if not ENV_PATH.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = ENV_PATH.with_name(f".env.bak_{ts}")
    dst.write_bytes(ENV_PATH.read_bytes())
    baks = sorted(ENV_PATH.parent.glob(".env.bak_*"))
    for old in baks[:-3]:
        old.unlink()
    return dst.name


def set_vars(values: Dict[str, str]) -> Dict[str, Any]:
    """Atualiza/insere NOME=valor no .env preservando as demais linhas; atualiza os.environ."""
    for n, v in values.items():
        if not re.fullmatch(r"[A-Z0-9_]+", n) or any(c in v for c in "\r\n"):
            raise ValueError(f"valor inválido para {n}")
    bak = _backup()
    raw = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines: List[str] = raw.splitlines()
    done = set()
    for i, ln in enumerate(lines):
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", ln)
        if m and m.group(1) in values:
            lines[i] = f"{m.group(1)}={values[m.group(1)]}"
            done.add(m.group(1))
    for n, v in values.items():
        if n not in done:
            lines.append(f"{n}={v}")
    ENV_PATH.write_text(nl.join(lines) + nl, encoding="utf-8")
    os.environ.update(values)
    return {"backup": bak, "names": list(values)}


def test_keys(mode: str, key: str, secret: str) -> Dict[str, Any]:
    """Chamadas SOMENTE leitura: get_api_key_information + get_wallet_balance(UNIFIED)."""
    out: Dict[str, Any] = {"mode": mode, "key_end": f"****{key[-4:]}", "ok": False}

    def clean(e):
        t = str(e).replace(key, "***").replace(secret, "***")
        return keys.scrub(t)

    http = HTTP(testnet=(mode == keys.TESTNET), api_key=key, api_secret=secret, recv_window=10000, timeout=15, max_retries=1)
    try:
        info = http.get_api_key_information().get("result", {})
        perms = {k: v for k, v in (info.get("permissions") or {}).items() if v}
        out["info"] = {"somente_leitura": bool(int(info.get("readOnly", 0) or 0)), "permissoes": perms,
                       "ips": info.get("ips") or [], "expira_em": info.get("expiredAt") or "sem data",
                       "dias_restantes": info.get("deadlineDay"), "unified": info.get("unified"), "uta": info.get("uta"),
                       "nota": info.get("note", "")}
    except Exception as e:
        out["error"] = clean(e)
        out["hint"] = hint_for(out["error"])
        return out
    try:
        lst = http.get_wallet_balance(accountType="UNIFIED").get("result", {}).get("list", [])
        w = lst[0] if lst else {}
        out["wallet"] = {"equity_total": w.get("totalEquity"), "disponivel": w.get("totalAvailableBalance")}
    except Exception as e:
        out["wallet_error"] = clean(e)
        out["wallet_hint"] = hint_for(out["wallet_error"])
    out["ok"] = True
    if out["info"]["somente_leitura"]:
        out["warn"] = "Chave SOMENTE LEITURA (readOnly=1): o painel lê saldo, mas NÃO consegue enviar ordens. Crie a chave com permissão de Trade."
    return out


def explain(msg: str) -> str:
    """Categoria curta do motivo de uma ordem nao ter ido."""
    m = str(msg)
    if "KILL" in m.upper():
        return "kill switch"
    if "não armada" in m or "CONFIRMAR" in m:
        return "conta real não confirmada"
    if "não configuradas" in m or any(c in m for c in ("10003", "10004", "10005", "10010", "33004")):
        return "chave inválida"
    if "mínimo" in m:
        return "valor abaixo do mínimo da Bybit"
    if any(s in m for s in ("Stake", "Perda diária", "posições abertas", "verificar o risco")):
        return "limite de risco"
    if "HOLD" in m:
        return "sinal HOLD"
    return "erro da Bybit"
