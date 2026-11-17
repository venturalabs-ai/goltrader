"""Suíte de opções: Black-Scholes (gregas), GEX por strike, gamma flip, IV smile, max pain, PCR, vol realizada.

Convenções (documentadas porque são PREMISSAS, não dados de posicionamento real):
- GEX por strike = gamma × OI × tamanho do contrato × spot² × 1%  (US$ de delta a (re)hedgear por 1% de movimento).
  Premissa clássica "dealers long calls / short puts": calls somam (+), puts subtraem (−). Não há dado real de
  posição de dealers; é uma estimativa.
- vega = por 1 ponto de vol (1%); theta = por dia; IV em decimal (0.50 = 50%).
"""
from __future__ import annotations

import math
import random
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

SECONDS_PER_YEAR = 365.0 * 24 * 3600
MONTHS = {m: i + 1 for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


@dataclass
class OptionContract:
    symbol: str
    base: str
    expiry_ts: int  # epoch s
    strike: float
    cp: str  # "C" | "P"
    iv: float  # decimal
    oi: float  # contratos
    bid: float = 0.0
    ask: float = 0.0
    mark: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    theta: float = 0.0


# ---------------------------------------------------------------- Black-Scholes
def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1d2(S: float, K: float, T: float, sigma: float, r: float):
    vs = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / vs
    return d1, d1 - vs


def bs_price(S: float, K: float, T: float, sigma: float, cp: str, r: float = 0.0) -> float:
    cp = cp.upper()
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0) if cp == "C" else max(K - S, 0.0)
    d1, d2 = _d1d2(S, K, T, sigma, r)
    if cp == "C":
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


def bs_gamma(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1, _ = _d1d2(S, K, T, sigma, r)
    return norm_pdf(d1) / (S * sigma * math.sqrt(T))


def bs_greeks(S: float, K: float, T: float, sigma: float, cp: str, r: float = 0.0) -> Dict[str, float]:
    cp = cp.upper()
    if T <= 0 or sigma <= 0:
        itm = (S > K) if cp == "C" else (S < K)
        return {"delta": (1.0 if cp == "C" else -1.0) if itm else 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    d1, d2 = _d1d2(S, K, T, sigma, r)
    pdf = norm_pdf(d1)
    delta = norm_cdf(d1) if cp == "C" else norm_cdf(d1) - 1.0
    gamma = pdf / (S * sigma * math.sqrt(T))
    vega = S * pdf * math.sqrt(T) / 100.0
    first = -S * pdf * sigma / (2 * math.sqrt(T))
    if cp == "C":
        theta = first - r * K * math.exp(-r * T) * norm_cdf(d2)
    else:
        theta = first + r * K * math.exp(-r * T) * norm_cdf(-d2)
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta / 365.0}


def implied_vol(price: float, S: float, K: float, T: float, cp: str, r: float = 0.0) -> Optional[float]:
    """IV por bisseção; None se o preço está fora dos limites de arbitragem."""
    if T <= 0 or price <= 0:
        return None
    lo, hi = 1e-4, 5.0
    if price < bs_price(S, K, T, lo, cp, r) or price > bs_price(S, K, T, hi, cp, r):
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, mid, cp, r) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------- parsing / chain
_SYM = re.compile(r"^([A-Z0-9]+)-(\d{1,2})([A-Z]{3})(\d{2})-(\d+(?:\.\d+)?)-([CP])(?:-[A-Z]+)?$")


def parse_bybit_option_symbol(sym: str):
    """'BTC-27DEC26-100000-C-USDT' -> (base, expiry_ts(08:00 UTC), strike, cp) ou None."""
    m = _SYM.match(sym.upper())
    if not m:
        return None
    base, day, mon, yy, strike, cp = m.groups()
    if mon not in MONTHS:
        return None
    try:
        dt = datetime(2000 + int(yy), MONTHS[mon], int(day), 8, 0, tzinfo=timezone.utc)
    except ValueError:
        return None
    return base, int(dt.timestamp()), float(strike), cp


def _f(x, default=0.0) -> float:
    try:
        return float(x) if x not in (None, "") else default
    except (TypeError, ValueError):
        return default


def chain_from_bybit(tickers: Sequence[Dict]) -> List[OptionContract]:
    """Converte /v5/market/tickers?category=option em contratos (IV/OI/gregas reais da API pública)."""
    out: List[OptionContract] = []
    for t in tickers:
        p = parse_bybit_option_symbol(str(t.get("symbol", "")))
        if not p:
            continue
        base, exp, strike, cp = p
        out.append(OptionContract(
            symbol=t["symbol"], base=base, expiry_ts=exp, strike=strike, cp=cp,
            iv=_f(t.get("markIv")), oi=_f(t.get("openInterest")),
            bid=_f(t.get("bid1Price")), ask=_f(t.get("ask1Price")), mark=_f(t.get("markPrice")),
            delta=_f(t.get("delta")), gamma=_f(t.get("gamma")), vega=_f(t.get("vega")), theta=_f(t.get("theta")),
        ))
    return out


def spot_from_bybit_tickers(tickers: Sequence[Dict]) -> Optional[float]:
    for t in tickers:
        v = _f(t.get("underlyingPrice") or t.get("indexPrice"))
        if v > 0:
            return v
    return None


def synthetic_chain(spot: float, now: Optional[float] = None, seed: int = 7, base: str = "BTC") -> List[OptionContract]:
    """Cadeia SINTÉTICA (rotulada SIMULADO na UI): smile paramétrico + OI aleatório. NÃO é dado de mercado."""
    now = now or time.time()
    rng = random.Random(seed)
    out: List[OptionContract] = []
    step = max(round(spot * 0.02, -2), 1.0)
    center = round(spot / step) * step
    day0 = int(now) - int(now) % 86400 + 8 * 3600
    for days in (3, 10, 31, 66):
        exp = day0 + days * 86400
        T = (exp - now) / SECONDS_PER_YEAR
        for k in range(-8, 9):
            strike = center + k * step
            m = math.log(strike / spot)
            iv = max(0.15, 0.50 + 0.9 * m * m / max(T, 0.02) ** 0.5 * 0.1 - 0.25 * m)
            for cp in ("C", "P"):
                g = bs_greeks(spot, strike, T, iv, cp)
                mark = bs_price(spot, strike, T, iv, cp)
                oi = round(rng.uniform(5, 400) * math.exp(-abs(m) * 6), 1)
                out.append(OptionContract(
                    symbol=f"{base}-SIM-{days}D-{int(strike)}-{cp}", base=base, expiry_ts=exp, strike=strike,
                    cp=cp, iv=iv, oi=oi, bid=mark * 0.98, ask=mark * 1.02, mark=mark,
                    delta=g["delta"], gamma=g["gamma"], vega=g["vega"], theta=g["theta"],
                ))
    return out


def years_to_expiry(expiry_ts: int, now: Optional[float] = None) -> float:
    return max((expiry_ts - (now or time.time())) / SECONDS_PER_YEAR, 0.0)


def list_expiries(chain: Sequence[OptionContract], now: Optional[float] = None) -> List[int]:
    now = now or time.time()
    return sorted({c.expiry_ts for c in chain if c.expiry_ts > now})


def select_expiry(chain: Sequence[OptionContract], now: Optional[float] = None, min_days: float = 1.0) -> Optional[int]:
    now = now or time.time()
    exps = list_expiries(chain, now)
    for e in exps:
        if (e - now) / 86400 >= min_days:
            return e
    return exps[-1] if exps else None


# ---------------------------------------------------------------- métricas
def pcr_oi(chain: Sequence[OptionContract], expiry_ts: Optional[int] = None) -> Optional[float]:
    sel = [c for c in chain if expiry_ts is None or c.expiry_ts == expiry_ts]
    calls = sum(c.oi for c in sel if c.cp == "C")
    puts = sum(c.oi for c in sel if c.cp == "P")
    return round(puts / calls, 3) if calls > 0 else None


def max_pain(chain: Sequence[OptionContract], expiry_ts: Optional[int] = None) -> Optional[float]:
    """Strike que minimiza o payoff total pago a compradores no vencimento (dor dos lançadores)."""
    sel = [c for c in chain if (expiry_ts is None or c.expiry_ts == expiry_ts) and c.oi > 0]
    if not sel:
        return None
    strikes = sorted({c.strike for c in sel})
    best_strike: Optional[float] = None
    best_pain = float("inf")
    for test_s in strikes:
        pain = 0.0
        for c in sel:
            if c.cp == "C":
                pain += max(0.0, test_s - c.strike) * c.oi
            else:
                pain += max(0.0, c.strike - test_s) * c.oi
        if pain < best_pain:
            best_pain = pain
            best_strike = test_s  # (bug do código original: atribuía o strike da linha, não o testado)
    return best_strike


def _contract_gamma(c: OptionContract, spot: float, now: float) -> float:
    if c.gamma > 0:
        return c.gamma
    T = years_to_expiry(c.expiry_ts, now)
    return bs_gamma(spot, c.strike, T, c.iv) if c.iv > 0 else 0.0


def gex_by_strike(
    chain: Sequence[OptionContract], spot: float, now: Optional[float] = None,
    contract_size: float = 1.0, expiry_ts: Optional[int] = None,
) -> List[Dict]:
    """GEX (US$ por 1% de movimento) por strike; calls +, puts − (premissa de dealers)."""
    now = now or time.time()
    rows: Dict[float, Dict[str, float]] = {}
    for c in chain:
        if expiry_ts is not None and c.expiry_ts != expiry_ts:
            continue
        if c.expiry_ts <= now or c.oi <= 0:
            continue
        g = _contract_gamma(c, spot, now)
        gex = g * c.oi * contract_size * spot * spot * 0.01
        r = rows.setdefault(c.strike, {"call_gex": 0.0, "put_gex": 0.0})
        if c.cp == "C":
            r["call_gex"] += gex
        else:
            r["put_gex"] -= gex
    return [
        {"strike": k, "call_gex": v["call_gex"], "put_gex": v["put_gex"], "net_gex": v["call_gex"] + v["put_gex"]}
        for k, v in sorted(rows.items())
    ]


def gamma_flip(
    chain: Sequence[OptionContract], spot: float, now: Optional[float] = None,
    contract_size: float = 1.0, span: float = 0.2, steps: int = 80,
) -> Optional[float]:
    """Nível de spot onde o GEX total (recalculado por Black-Scholes) troca de sinal, mais próximo do spot."""
    now = now or time.time()
    live = [c for c in chain if c.expiry_ts > now and c.oi > 0 and c.iv > 0]
    if not live:
        return None

    def total(S: float) -> float:
        t = 0.0
        for c in live:
            g = bs_gamma(S, c.strike, years_to_expiry(c.expiry_ts, now), c.iv)
            sgn = 1.0 if c.cp == "C" else -1.0
            t += sgn * g * c.oi * contract_size * S * S * 0.01
        return t

    grid = [spot * (1 - span + 2 * span * i / steps) for i in range(steps + 1)]
    vals = [total(s) for s in grid]
    crossings = []
    for i in range(steps):
        a, b = vals[i], vals[i + 1]
        if a == 0:
            crossings.append(grid[i])
        elif a * b < 0:
            crossings.append(grid[i] + (grid[i + 1] - grid[i]) * (-a) / (b - a))
    if not crossings:
        return None
    return round(min(crossings, key=lambda x: abs(x - spot)), 2)


def iv_smile(chain: Sequence[OptionContract], spot: float, expiry_ts: int) -> List[Dict]:
    """IV (%) por strike usando o lado OTM (puts abaixo do spot, calls acima) do vencimento escolhido."""
    by: Dict[float, List[float]] = {}
    for c in chain:
        if c.expiry_ts != expiry_ts or c.iv <= 0:
            continue
        otm = (c.cp == "P" and c.strike <= spot) or (c.cp == "C" and c.strike >= spot)
        if otm:
            by.setdefault(c.strike, []).append(c.iv)
    return [{"strike": k, "iv_pct": round(sum(v) / len(v) * 100, 2)} for k, v in sorted(by.items())]


def atm_iv(chain: Sequence[OptionContract], spot: float, expiry_ts: int) -> Optional[float]:
    """IV ATM (% a.a.): média das IVs dos dois strikes mais próximos do spot."""
    sel = [c for c in chain if c.expiry_ts == expiry_ts and c.iv > 0]
    if not sel:
        return None
    sel.sort(key=lambda c: abs(c.strike - spot))
    near = sel[:4]
    return round(sum(c.iv for c in near) / len(near) * 100, 2)


def realized_vol(candles: Sequence[Dict], periods_per_year: float = 525600.0, min_returns: int = 30) -> Optional[Dict]:
    """Vol realizada anualizada (%) dos retornos log de fechamento das velas (1m por padrão)."""
    closes = [c["close"] for c in candles if c.get("close", 0) > 0]
    if len(closes) - 1 < min_returns:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return {"rv_pct": round(math.sqrt(var) * math.sqrt(periods_per_year) * 100, 2), "n_returns": len(rets)}


def compute_options_analytics(
    chain: Sequence[OptionContract], spot: float, source: str, now: Optional[float] = None,
    contract_size: float = 1.0, max_chain_rows: int = 120,
) -> Optional[Dict]:
    """Pacote para a UI. `source`: 'BYBIT' (API pública real) ou 'SIMULADO'."""
    now = now or time.time()
    expiry = select_expiry(chain, now)
    if expiry is None or spot <= 0:
        return None
    gex = gex_by_strike(chain, spot, now, contract_size)  # agregado em todos os vencimentos
    near = [r for r in gex if abs(r["strike"] / spot - 1) <= 0.25]
    sel = [c for c in chain if c.expiry_ts == expiry and abs(c.strike / spot - 1) <= 0.25]
    sel.sort(key=lambda c: (c.strike, c.cp))
    return {
        "source": source,
        "simulated": source != "BYBIT",
        "spot": round(spot, 2),
        "contract_size": contract_size,
        "gex_assumption": "calls + / puts − (premissa dealers long calls/short puts; sem dado real de posição)",
        "expiry_ts": expiry,
        "expiry_days": round((expiry - now) / 86400, 2),
        "expiries": list_expiries(chain, now),
        "atm_iv_pct": atm_iv(chain, spot, expiry),
        "pcr_oi": pcr_oi(chain, expiry),
        "max_pain": max_pain(chain, expiry),
        "gamma_flip": gamma_flip(chain, spot, now, contract_size),
        "net_gex_total": round(sum(r["net_gex"] for r in gex), 2),
        "gex_by_strike": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()} for r in near],
        "iv_smile": iv_smile(chain, spot, expiry),
        "chain": [asdict(c) for c in sel[:max_chain_rows]],
        "updated_at": int(now * 1000),
    }


def iv_rv_premium(atm_iv_pct: Optional[float], rv_pct: Optional[float]) -> Optional[float]:
    """Prêmio IV−RV em pontos de vol; None se faltar IV ou RV (nunca texto fixo)."""
    if atm_iv_pct is None or rv_pct is None:
        return None
    return round(atm_iv_pct - rv_pct, 2)
