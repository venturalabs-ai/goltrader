"""Scanner de oportunidades: detectores de mercado (dados reais Bybit) + validacao walk-forward.

Toda entrada precisa de uma metodologia com EV fora-da-amostra positivo dando sinal no candle atual.
Os detectores (queda/desvalorizacao, sobrecompra, volume, rompimento, funding, livro) so reforcam
ou penalizam o score — nunca entram sozinhos.
"""
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from dashboard_components import engine as E


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    l_ = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean().replace(0, 1e-9)
    return 100 - 100 / (1 + g / l_)


def detectors(df: pd.DataFrame, funding: Optional[float] = None, ob_imb: Optional[float] = None) -> Dict[str, Any]:
    """Usa so candles FECHADOS (descarta o ultimo, em formacao). Retorna metricas, hits e forca por direcao."""
    d = df.iloc[:-1]
    c, h, l_, v = d["close"], d["high"], d["low"], d["volume"]
    last = float(c.iloc[-1])
    ret15 = (last / float(c.iloc[-16]) - 1) * 100 if len(c) > 16 else 0.0
    hi60 = float(h.tail(60).max())
    lo60 = float(l_.tail(60).min())
    dd60 = (last / hi60 - 1) * 100
    up60 = (last / lo60 - 1) * 100
    tp = (h + l_ + c) / 3
    vwap = float((tp * v).tail(30).sum() / max(v.tail(30).sum(), 1e-9))
    sd = float(tp.tail(30).std() or 1e-9)
    z = (last - vwap) / sd
    sma, std = c.rolling(20).mean(), c.rolling(20).std()
    bb_low, bb_up = float(sma.iloc[-1] - 2 * std.iloc[-1]), float(sma.iloc[-1] + 2 * std.iloc[-1])
    rsi = float(_rsi(c).iloc[-1])
    vol_ratio = float(v.iloc[-1] / max(v.iloc[-21:-1].mean(), 1e-9))
    brk_up = last > float(h.iloc[-21:-1].max())
    brk_dn = last < float(l_.iloc[-21:-1].min())

    bull, bear, hits = 0.0, 0.0, []
    # desvalorizacao / sobrevenda
    if ret15 <= -1.0 or dd60 <= -2.0:
        bull += 0.25; hits.append(f"queda {ret15:+.1f}% (15c) / {dd60:+.1f}% do topo")
    if z <= -2.0 or last < bb_low:
        bull += 0.25; hits.append(f"z-VWAP {z:+.1f}" + (" abaixo BB" if last < bb_low else ""))
    if rsi <= 25:
        bull += 0.25; hits.append(f"RSI {rsi:.0f}")
    # sobrecompra / pico
    if ret15 >= 1.0 or up60 >= 2.0:
        bear += 0.25; hits.append(f"alta {ret15:+.1f}% (15c) / {up60:+.1f}% do fundo")
    if z >= 2.0 or last > bb_up:
        bear += 0.25; hits.append(f"z-VWAP {z:+.1f}" + (" acima BB" if last > bb_up else ""))
    if rsi >= 75:
        bear += 0.25; hits.append(f"RSI {rsi:.0f}")
    # rompimento de momentum
    if brk_up:
        bull += 0.2; hits.append("rompeu máxima 20c")
    if brk_dn:
        bear += 0.2; hits.append("perdeu mínima 20c")
    # volume anomalo amplifica o lado dominante
    if vol_ratio >= 2.5:
        hits.append(f"volume {vol_ratio:.1f}x")
        bull, bear = bull * 1.2, bear * 1.2
    # funding (crowding) e livro de ofertas
    if funding is not None:
        if funding <= -0.0005:
            bull += 0.15; hits.append(f"funding {funding * 100:+.3f}%")
        elif funding >= 0.0005:
            bear += 0.15; hits.append(f"funding {funding * 100:+.3f}%")
    if ob_imb is not None:
        if ob_imb >= 0.25:
            bull += 0.1; hits.append(f"livro comprador {ob_imb:+.2f}")
        elif ob_imb <= -0.25:
            bear += 0.1; hits.append(f"livro vendedor {ob_imb:+.2f}")
    return {"ret15": round(ret15, 3), "dd60": round(dd60, 3), "z_vwap": round(z, 2), "rsi": round(rsi, 1),
            "vol_ratio": round(vol_ratio, 2), "funding": funding, "ob_imb": ob_imb,
            "bull": round(min(bull, 1.0), 3), "bear": round(min(bear, 1.0), 3), "hits": hits}


def evaluate_symbol(df: pd.DataFrame, board: List[Dict[str, Any]], det: Dict[str, Any], *, min_ev: float,
                    min_trades: int, require_bonf: bool) -> Dict[str, Any]:
    """Votos das metodologias VALIDADAS (EV OOS > min_ev) que dao sinal agora, ponderados por EV."""
    votes, used = 0.0, []
    for r in board:
        if r.get("trades", 0) < min_trades or r.get("EV_por_trade", -1) <= min_ev:
            continue
        if require_bonf and not r.get("aprovado_bonferroni"):
            continue
        try:
            sig = int(E.STRATS[r["strategy"]](params=r.get("best_params") or {}).generate_signals(df).iloc[-1])
        except Exception:
            continue
        if sig == 0:
            continue
        w = float(r["EV_por_trade"]) * (1.25 if r.get("aprovado_bonferroni") else 1.0)
        votes += sig * w
        used.append({"strategy": r["strategy"], "sig": sig, "EV": r["EV_por_trade"], "wr": r["win_rate"],
                     "p": r["p_value"], "bonf": bool(r.get("aprovado_bonferroni"))})
    if not used:
        return {"direction": 0, "score": 0.0, "decision": "HOLD", "reason": "nenhuma metodologia validada com sinal", "methods": []}
    if abs(votes) < 1e-9:
        return {"direction": 0, "score": 0.0, "decision": "HOLD", "reason": "sinais validados em conflito", "methods": used}
    direction = 1 if votes > 0 else -1
    aligned = [u for u in used if u["sig"] == direction]
    best = max(aligned, key=lambda u: (u["bonf"], u["EV"], u["wr"], -u["p"]))
    opp_al = det["bull"] if direction == 1 else det["bear"]
    opp_op = det["bear"] if direction == 1 else det["bull"]
    edge = float(best["EV"]) + (0.2 if best["bonf"] else 0.0)
    score = edge * (1 + 0.5 * opp_al - 0.5 * opp_op) * (1 + 0.1 * (len(aligned) - 1))
    return {"direction": direction, "score": round(score, 4), "decision": "CALL" if direction == 1 else "PUT",
            "reason": f"{len(aligned)} metodologia(s) validada(s), melhor {best['strategy']} EV {best['EV']:+.3f}",
            "methods": used, "best": best, "opp_aligned": opp_al, "opp_opposed": opp_op}


# ---------------- SPOT (long-only) ----------------
def long_stats(df_oos: pd.DataFrame, strat_name: str, params: dict, expiry: int, fee_pct: float) -> Dict[str, Any]:
    """Retorno REAL das entradas de compra (sinal +1) na janela fora-da-amostra, liquido de taxas (ida+volta)."""
    sig = E.STRATS[strat_name](params=params or {}).generate_signals(df_oos)
    ent = df_oos["open"]
    ext = df_oos["close"].shift(-(expiry - 1))
    m = (sig == 1) & ent.notna() & ext.notna()
    r = (ext[m] / ent[m] - 1.0) * 100.0
    n = int(len(r))
    if n == 0:
        return {"long_n": 0, "long_wr": 0.0, "long_ret_pct": 0.0, "long_net_pct": -2 * fee_pct}
    net = float(r.mean()) - 2 * fee_pct
    return {"long_n": n, "long_wr": round(float((r > 2 * fee_pct).mean()), 4), "long_ret_pct": round(float(r.mean()), 4),
            "long_net_pct": round(net, 4)}


def evaluate_long(df: pd.DataFrame, board: List[Dict[str, Any]], det: Dict[str, Any], *, min_net: float,
                  min_long_trades: int, require_bonf: bool, has_position: bool) -> Dict[str, Any]:
    """Spot: so compra. Metodologias validadas = edge liquido de taxas > min_net na OOS.
    Sinal de venda das validadas so serve para sair antes de uma posicao do robo."""
    buy, sell = [], []
    for r in board:
        if r.get("long_n", 0) < min_long_trades or r.get("long_net_pct", -9) <= min_net:
            continue
        if require_bonf and not r.get("aprovado_bonferroni"):
            continue
        try:
            sig = int(E.STRATS[r["strategy"]](params=r.get("best_params") or {}).generate_signals(df).iloc[-1])
        except Exception:
            continue
        u = {"strategy": r["strategy"], "sig": sig, "net": r["long_net_pct"], "gross": r["long_ret_pct"],
             "wr": r.get("long_wr"), "n": r["long_n"], "bonf": bool(r.get("aprovado_bonferroni"))}
        if sig == 1:
            buy.append(u)
        elif sig == -1:
            sell.append(u)
    w_buy = sum(u["net"] for u in buy)
    w_sell = sum(u["net"] for u in sell)
    if has_position and sell and w_sell >= w_buy:
        return {"decision": "EXIT", "score": 0.0, "reason": f"{len(sell)} validada(s) com sinal de venda — saída antecipada", "methods": sell}
    if not buy:
        if sell:
            return {"decision": "HOLD", "score": 0.0, "reason": "PUT ignorado (spot não vende a descoberto)", "methods": sell}
        return {"decision": "HOLD", "score": 0.0, "reason": "nenhuma metodologia validada (líquida de taxas) com sinal de compra", "methods": []}
    if w_sell >= w_buy:
        return {"decision": "HOLD", "score": 0.0, "reason": "sinais validados em conflito", "methods": buy + sell}
    best = max(buy, key=lambda u: (u["bonf"], u["net"], u["n"]))
    score = best["net"] * (1 + 0.5 * det["bull"] - 0.5 * det["bear"]) * (1 + 0.1 * (len(buy) - 1)) + (0.05 if best["bonf"] else 0)
    return {"decision": "CALL", "score": round(score, 4), "best": best, "methods": buy,
            "reason": f"{len(buy)} validada(s) p/ compra; melhor {best['strategy']} edge líq {best['net']:+.3f}% (bruto {best['gross']:+.3f}%)"}
