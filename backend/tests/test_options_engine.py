import math

import pytest

from options_engine import (OptionContract, atm_iv, bs_greeks, bs_price, chain_from_bybit, compute_options_analytics,
                            gamma_flip, gex_by_strike, implied_vol, iv_rv_premium, iv_smile, max_pain, parse_bybit_option_symbol,
                            pcr_oi, realized_vol, select_expiry, synthetic_chain)

NOW = 1_800_000_000.0
EXP = int(NOW + 30 * 86400)


def oc(strike, cp, oi, iv=0.5, exp=EXP, gamma=0.0):
    return OptionContract(symbol=f"X-{strike}-{cp}", base="X", expiry_ts=exp, strike=float(strike), cp=cp, iv=iv, oi=oi, gamma=gamma)


def test_put_call_parity():
    S, K, T, sig, r = 100.0, 95.0, 0.5, 0.4, 0.03
    c, p = bs_price(S, K, T, sig, "C", r), bs_price(S, K, T, sig, "P", r)
    assert c - p == pytest.approx(S - K * math.exp(-r * T), abs=1e-9)


def test_greeks_sanity():
    S, K, T, sig = 100.0, 100.0, 0.25, 0.5
    gc, gp = bs_greeks(S, K, T, sig, "C"), bs_greeks(S, K, T, sig, "P")
    assert 0 < gc["delta"] < 1 and -1 < gp["delta"] < 0
    assert gc["delta"] - gp["delta"] == pytest.approx(1.0, abs=1e-9)
    assert gc["gamma"] == pytest.approx(gp["gamma"]) and gc["gamma"] > 0
    assert gc["vega"] > 0 and gc["theta"] < 0
    # delta ≈ derivada numérica do preço
    h = 0.01
    num = (bs_price(S + h, K, T, sig, "C") - bs_price(S - h, K, T, sig, "C")) / (2 * h)
    assert gc["delta"] == pytest.approx(num, abs=1e-5)


def test_implied_vol_roundtrip():
    px = bs_price(100, 110, 0.3, 0.62, "C")
    assert implied_vol(px, 100, 110, 0.3, "C") == pytest.approx(0.62, abs=1e-4)
    assert implied_vol(0.0, 100, 110, 0.3, "C") is None


def test_max_pain_hand_computed():
    chain = [oc(100, "C", 5), oc(110, "C", 10), oc(120, "C", 20), oc(100, "P", 20), oc(110, "P", 10), oc(120, "P", 5)]
    # dor total: 100 -> 200 ; 110 -> 100 ; 120 -> 200
    assert max_pain(chain) == 110.0


def test_max_pain_returns_tested_strike_not_last_row():
    """Regressão do bug original (atribuía o strike da linha do DataFrame, não o strike testado)."""
    chain = [oc(90, "P", 1), oc(100, "C", 10), oc(100, "P", 10), oc(110, "C", 1)]
    assert max_pain(chain) == 100.0
    assert max_pain(list(reversed(chain))) == 100.0  # independe da ordem das linhas
    assert max_pain([]) is None


def test_max_pain_per_expiry():
    other = int(NOW + 60 * 86400)
    chain = [oc(100, "C", 10), oc(100, "P", 10), oc(200, "C", 10, exp=other), oc(200, "P", 10, exp=other)]
    assert max_pain(chain, EXP) == 100.0 and max_pain(chain, other) == 200.0


def test_pcr():
    assert pcr_oi([oc(100, "C", 10), oc(100, "P", 5)]) == 0.5
    assert pcr_oi([oc(100, "P", 5)]) is None


def test_gex_sign_convention():
    rows = gex_by_strike([oc(100, "C", 10), oc(100, "P", 10), oc(90, "P", 5)], 100.0, NOW)
    by = {r["strike"]: r for r in rows}
    assert by[100.0]["call_gex"] > 0 and by[100.0]["put_gex"] < 0
    assert by[100.0]["net_gex"] == pytest.approx(0.0, abs=1e-6)  # mesmo gamma/OI se cancela
    assert by[90.0]["net_gex"] < 0
    # usa o gamma informado (Bybit) quando existe
    rows = gex_by_strike([oc(100, "C", 10, gamma=0.02)], 100.0, NOW)
    assert rows[0]["call_gex"] == pytest.approx(0.02 * 10 * 100 * 100 * 0.01)


def test_gamma_flip_between_put_and_call_walls():
    chain = [oc(110, "C", 100), oc(90, "P", 100)]
    flip = gamma_flip(chain, 100.0, NOW)
    assert flip is not None and 95 < flip < 105
    assert gamma_flip([oc(110, "C", 100)], 100.0, NOW) is None  # só calls: nunca troca de sinal
    assert gamma_flip([], 100.0, NOW) is None


def test_iv_smile_uses_otm_side():
    chain = [oc(90, "P", 1, iv=0.6), oc(90, "C", 1, iv=0.99), oc(110, "C", 1, iv=0.55), oc(110, "P", 1, iv=0.99)]
    sm = {r["strike"]: r["iv_pct"] for r in iv_smile(chain, 100.0, EXP)}
    assert sm == {90.0: 60.0, 110.0: 55.0}
    assert atm_iv([oc(100, "C", 1, iv=0.5), oc(100, "P", 1, iv=0.5)], 100.0, EXP) == 50.0


def test_realized_vol_from_candles():
    flat = [{"close": 100.0}] * 40
    assert realized_vol(flat)["rv_pct"] == 0.0
    assert realized_vol(flat[:10]) is None  # poucos dados -> None (nunca inventa)
    up = [{"close": 100.0 * (1.001 if i % 2 else 0.999)} for i in range(60)]
    assert realized_vol(up)["rv_pct"] > 0


def test_iv_rv_premium_computed_not_literal():
    assert iv_rv_premium(52.4, 44.5) == 7.9
    assert iv_rv_premium(None, 44.5) is None and iv_rv_premium(52.4, None) is None


def test_parse_bybit_symbol_and_chain():
    base, exp, k, cp = parse_bybit_option_symbol("BTC-27DEC26-100000-C-USDT")
    assert (base, k, cp) == ("BTC", 100000.0, "C")
    from datetime import datetime, timezone
    assert exp == int(datetime(2026, 12, 27, 8, 0, tzinfo=timezone.utc).timestamp())
    assert parse_bybit_option_symbol("BTC-6NOV26-95000-P")[3] == "P"
    assert parse_bybit_option_symbol("BTCUSDT") is None
    chain = chain_from_bybit([
        {"symbol": "BTC-27DEC26-100000-C-USDT", "markIv": "0.55", "openInterest": "12.5", "bid1Price": "100", "ask1Price": "110",
         "markPrice": "105", "delta": "0.4", "gamma": "0.00001", "vega": "30", "theta": "-20"},
        {"symbol": "lixo"},
    ])
    assert len(chain) == 1 and chain[0].iv == 0.55 and chain[0].oi == 12.5 and chain[0].gamma == 0.00001


def test_synthetic_chain_and_analytics_label():
    chain = synthetic_chain(65000.0, now=NOW)
    a = compute_options_analytics(chain, 65000.0, "SIMULADO", now=NOW)
    assert a["simulated"] is True and a["source"] == "SIMULADO"
    assert a["max_pain"] is not None and a["atm_iv_pct"] and a["gex_by_strike"] and a["iv_smile"] and a["chain"]
    assert select_expiry(chain, NOW) is not None
    real = compute_options_analytics(chain, 65000.0, "BYBIT", now=NOW)
    assert real["simulated"] is False
