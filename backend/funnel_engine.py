"""Algoritmos puros do Funil de Confluência (sem rede, sem estado global).

Origem: código colado por Wemerson (t313u). Alterações: `venues` do sinal é parâmetro; helpers
`vwap_zscore` e `inside_confirmed_ob` para ligar o funil a candles/OBs reais.
Nota: `detect_order_blocks` analisa as 5 últimas velas do array recebido (velas de 1m no feed atual);
a rotulagem "5m confirmado no 1m" do desenho original ainda NÃO é multi-timeframe (TODO).
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict
from typing import Dict, List, Optional, Sequence

from models import OrderBlock


def classify_buildup(price_delta: float, oi_delta: float) -> str:
    if price_delta > 0 and oi_delta > 0:
        return "LONG BUILDUP"
    elif price_delta < 0 and oi_delta > 0:
        return "SHORT BUILDUP"
    elif price_delta > 0 and oi_delta < 0:
        return "SHORT COVERING"
    return "LONG UNWINDING"


def detect_order_blocks(symbol: str, candles: List[Dict]) -> List[Dict]:
    """Detecta Order Blocks (última vela contrária antes de deslocamento forte) com distância % e reteste."""
    if len(candles) < 6:
        return []
    current_price = candles[-1]["close"]
    obs: List[Dict] = []

    for i in range(len(candles) - 5, len(candles) - 1):
        c_prev = candles[i - 1]
        c_curr = candles[i]
        c_next = candles[i + 1]

        # Bullish OB: última vela de baixa antes de deslocamento forte de alta
        if c_prev["close"] < c_prev["open"] and c_curr["close"] > c_prev["high"] * 1.0015:
            dist = ((current_price - c_prev["high"]) / current_price) * 100
            retested = any(k["low"] <= c_prev["high"] for k in candles[i + 1:])
            obs.append(asdict(OrderBlock(
                id=f"OB-BULL-{i}", symbol=symbol, side="BUY",
                top=round(c_prev["high"], 2), bottom=round(c_prev["low"], 2),
                status="CONFIRMED" if c_next["close"] > c_prev["high"] else "FORMING",
                retested=retested, distance_pct=round(dist, 2), timestamp=c_prev["time"],
            )))

        # Bearish OB: última vela de alta antes de deslocamento forte de baixa
        elif c_prev["close"] > c_prev["open"] and c_curr["close"] < c_prev["low"] * 0.9985:
            dist = ((c_prev["low"] - current_price) / current_price) * 100
            retested = any(k["high"] >= c_prev["low"] for k in candles[i + 1:])
            obs.append(asdict(OrderBlock(
                id=f"OB-BEAR-{i}", symbol=symbol, side="SELL",
                top=round(c_prev["high"], 2), bottom=round(c_prev["low"], 2),
                status="CONFIRMED" if c_next["close"] < c_prev["low"] else "FORMING",
                retested=retested, distance_pct=round(dist, 2), timestamp=c_prev["time"],
            )))
    return obs[-4:]


def inside_confirmed_ob(price: float, order_blocks: Sequence[Dict]) -> Optional[str]:
    """'BUY'/'SELL' se o preço está dentro de um OB CONFIRMED; senão None."""
    for ob in order_blocks:
        if ob.get("status") == "CONFIRMED" and ob["bottom"] <= price <= ob["top"]:
            return ob["side"]
    return None


def vwap_zscore(candles: Sequence[Dict], window: int = 60) -> Optional[float]:
    """Z-score do último fechamento vs VWAP da janela (ponderado por volume; sem volume = pesos iguais)."""
    data = list(candles)[-window:]
    if len(data) < 20:
        return None
    typ = [(c["high"] + c["low"] + c["close"]) / 3.0 for c in data]
    w = [max(float(c.get("volume", 0.0) or 0.0), 0.0) for c in data]
    if sum(w) <= 0:
        w = [1.0] * len(data)
    sw = sum(w)
    vwap = sum(t * x for t, x in zip(typ, w)) / sw
    var = sum(x * (t - vwap) ** 2 for t, x in zip(typ, w)) / sw
    std = math.sqrt(var)
    if std <= 0:
        return 0.0
    return (data[-1]["close"] - vwap) / std


def evaluate_inefficiency_funnel(
    symbol: str,
    price: float,
    vwap_deviation_zscore: float,
    book_imbalance: float,
    inside_confirmed_ob: Optional[str],
    news_sentiment_score: float,
    liquidation_spike_usd: float,
    venues: Optional[List[str]] = None,
) -> Optional[Dict]:
    """Funil de confluência de 5 estágios; autoriza sinal quando |score| >= 75."""
    score = 0
    reasons: List[str] = []

    # 1. Desvalorização momentânea / exaustão estatística (|Z| > 2.2 da VWAP)
    if vwap_deviation_zscore <= -2.2:
        score += 30
        reasons.append(f"Desvalorização Momentânea (Z={vwap_deviation_zscore:.2f})")
    elif vwap_deviation_zscore >= 2.2:
        score -= 30
        reasons.append(f"Sobrecompra Extrema (Z={vwap_deviation_zscore:.2f})")

    # 2. Escora no Order Book Wall
    if book_imbalance >= 1.65:
        score += 25
        reasons.append(f"Parede de Compra Absorvendo ({book_imbalance:.2f}x)")
    elif book_imbalance <= 0.60:
        score -= 25
        reasons.append(f"Parede de Venda Dominante ({book_imbalance:.2f}x)")

    # 3. Reteste de Order Block confirmado
    if inside_confirmed_ob == "BUY":
        score += 25
        reasons.append("Toque em Order Block Institucional de Compra")
    elif inside_confirmed_ob == "SELL":
        score -= 25
        reasons.append("Toque em Order Block Institucional de Venda")

    # 4. Varrida de liquidez (wipe-out)
    if liquidation_spike_usd > 150000:
        if score > 0:
            score += 10
            reasons.append("Exaustão de Stops Vendidos (Wipe-out)")
        else:
            score -= 10
            reasons.append("Exaustão de Stops Comprados (Wipe-out)")

    # 5. Notícias em tempo real (só reforça direção já formada)
    if news_sentiment_score > 0.3 and score > 0:
        score += 10
        reasons.append("Catalisador de Notícia Positivo")
    elif news_sentiment_score < -0.3 and score < 0:
        score -= 10
        reasons.append("Catalisador de Notícia Negativo")

    if abs(score) >= 75:
        return {
            "symbol": symbol,
            "direction": "BUY" if score > 0 else "SELL",
            "confidence": min(abs(score), 99),
            "price": price,
            "reasons": reasons,
            "venues": list(venues or ["BYBIT_PERP"]),
            "timestamp": int(time.time() * 1000),
        }
    return None
