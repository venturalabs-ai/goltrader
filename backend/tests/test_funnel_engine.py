from funnel_engine import (classify_buildup, detect_order_blocks, evaluate_inefficiency_funnel,
                           inside_confirmed_ob, vwap_zscore)


def candle(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c}


def test_classify_buildup_four_classes():
    assert classify_buildup(1, 1) == "LONG BUILDUP"
    assert classify_buildup(-1, 1) == "SHORT BUILDUP"
    assert classify_buildup(1, -1) == "SHORT COVERING"
    assert classify_buildup(-1, -1) == "LONG UNWINDING"


def test_detect_order_blocks_needs_min_candles():
    assert detect_order_blocks("BTCUSDT", [candle(i, 1, 1, 1, 1) for i in range(5)]) == []


def _flat(n, price=100.0):
    return [candle(i * 60, price, price + 0.1, price - 0.1, price) for i in range(n)]


def test_bullish_order_block_confirmed_and_retested():
    cs = _flat(6)
    # índice 6: vela de baixa (OB); 7: deslocamento forte de alta; 8: segue acima; 9: toca o topo do OB
    cs += [candle(360, 100, 100.2, 99.0, 99.2),
           candle(420, 99.2, 101.5, 99.2, 101.4),
           candle(480, 101.4, 102.0, 101.3, 101.9),
           candle(540, 101.9, 102.0, 100.1, 101.0)]
    obs = detect_order_blocks("BTCUSDT", cs)
    bull = [o for o in obs if o["side"] == "BUY"]
    assert bull, obs
    ob = bull[0]
    assert ob["status"] == "CONFIRMED" and ob["top"] == 100.2 and ob["bottom"] == 99.0
    assert isinstance(ob["retested"], bool)
    assert isinstance(ob["distance_pct"], float)


def test_bearish_order_block_detected():
    cs = _flat(6)
    cs += [candle(360, 100, 101.0, 99.9, 100.8),
           candle(420, 100.8, 100.8, 98.5, 98.6),
           candle(480, 98.6, 98.7, 97.9, 98.0),
           candle(540, 98.0, 98.2, 97.5, 97.8)]
    obs = detect_order_blocks("BTCUSDT", cs)
    assert any(o["side"] == "SELL" and o["status"] == "CONFIRMED" for o in obs), obs


def test_inside_confirmed_ob():
    obs = [{"side": "BUY", "status": "CONFIRMED", "bottom": 99, "top": 100},
           {"side": "SELL", "status": "FORMING", "bottom": 110, "top": 111}]
    assert inside_confirmed_ob(99.5, obs) == "BUY"
    assert inside_confirmed_ob(110.5, obs) is None
    assert inside_confirmed_ob(105, obs) is None


def test_funnel_threshold_buy_signal():
    sig = evaluate_inefficiency_funnel("BTCUSDT", 65000, -2.5, 1.8, "BUY", 0.5, 200000)
    assert sig and sig["direction"] == "BUY" and sig["confidence"] == 99  # 30+25+25+10+10=100 -> teto 99
    assert sig["venues"] == ["BYBIT_PERP"]
    assert len(sig["reasons"]) == 5


def test_funnel_below_threshold_returns_none():
    # 30 + 25 + 10 (liquidez só com score>0) = 65 < 75
    assert evaluate_inefficiency_funnel("BTCUSDT", 65000, -2.5, 1.8, None, 0.0, 200000) is None
    assert evaluate_inefficiency_funnel("BTCUSDT", 65000, 0.0, 1.0, None, 0.0, 0.0) is None


def test_funnel_exact_threshold_75_sell():
    sig = evaluate_inefficiency_funnel("X", 1.0, 2.5, 0.5, "SELL", 0.0, 0.0)  # -30-25-25 = -80
    assert sig and sig["direction"] == "SELL" and sig["confidence"] == 80
    sig = evaluate_inefficiency_funnel("X", 1.0, 0.0, 0.5, "SELL", -0.5, 200000)  # -25-25-10-10 = -70 -> None
    assert sig is None


def test_news_default_zero_does_not_change_score():
    base = evaluate_inefficiency_funnel("X", 1.0, -2.5, 1.8, "BUY", 0.0, 0.0)  # 80
    boosted = evaluate_inefficiency_funnel("X", 1.0, -2.5, 1.8, "BUY", 0.5, 0.0)  # 90
    assert base["confidence"] == 80 and boosted["confidence"] == 90


def test_vwap_zscore():
    cs = [candle(i, 100, 100.5, 99.5, 100 + (0.1 if i % 2 else -0.1)) for i in range(40)]
    cs[-1] = candle(40, 100, 90, 89, 89.5)
    z = vwap_zscore(cs)
    assert z is not None and z < -2
    assert vwap_zscore(cs[:10]) is None
