import time
from email.utils import formatdate

import pytest

from news_scanner import (GlobalMarketScanner, detect_assets, get_economic_calendar, item_id, parse_rss,
                          score_sentiment, set_calendar_provider)


def rss(*items):
    body = "".join(
        f"<item><title>{t}</title><link>{l}</link><description>{d}</description><pubDate>{formatdate(time.time())}</pubDate></item>"
        for t, l, d in items
    )
    return f"<?xml version='1.0'?><rss><channel>{body}</channel></rss>".encode()


def test_sentiment_scoring():
    assert score_sentiment("Bitcoin surges to record high as ETF inflows rally") > 0.5
    assert score_sentiment("Exchange hacked, bitcoin plunges amid liquidations") < -0.5
    assert score_sentiment("Bitcoin conference scheduled for Tuesday") == 0.0  # sem palavras = 0.0, nada inventado
    assert score_sentiment("Bitcoin sobe após aprovação") > 0
    assert score_sentiment("") == 0.0
    mixed = score_sentiment("surge but also crash")
    assert mixed == 0.0


def test_no_substring_false_positive():
    assert score_sentiment("bandwidth") == 0.0  # 'ban' não casa dentro de 'bandwidth'
    assert score_sentiment("Exchange bans withdrawals") < 0


def test_asset_detection():
    assert detect_assets("Bitcoin and Ether climb") == ["BTC", "ETH"]
    assert "XAU" in detect_assets("Ouro atinge nova máxima") and "USD" in detect_assets("Dólar e Fed")
    assert detect_assets("Nothing relevant here") == []


def test_parse_rss_and_link_sanitization():
    items = parse_rss(rss(("Bitcoin surges <b>hard</b>", "https://x.com/a", "ETF inflows"),
                          ("Evil", "javascript:alert(1)", "hack")), "Teste")
    assert items[0]["title"] == "Bitcoin surges hard" and items[0]["sentiment"] > 0 and "BTC" in items[0]["assets"]
    assert items[0]["link"] == "https://x.com/a"
    assert items[1]["link"] == ""  # esquema inseguro descartado


def test_parse_rss_rejects_entity_bomb():
    bomb = b"""<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><rss><channel><item><title>&b;</title></item></channel></rss>"""
    with pytest.raises(ValueError):
        parse_rss(bomb, "x")
    with pytest.raises(ValueError):
        parse_rss(b"<rss><unclosed>", "x")


def test_id_is_hash_of_title():
    assert item_id("Hello") == item_id("  hello ") and item_id("a") != item_id("b")


async def test_scanner_dedupes_and_survives_feed_failure():
    calls = {"n": 0}

    async def fetcher(url):
        calls["n"] += 1
        if "bad" in url:
            raise RuntimeError("boom")
        return rss(("Bitcoin surges on ETF inflows", "https://x/1", ""), ("Bitcoin surges on ETF inflows", "https://x/2", ""))

    sc = GlobalMarketScanner(feeds={"A": "http://a", "B": "http://a2", "C": "http://bad"}, fetcher=fetcher)
    new = await sc.poll_once()
    assert new == 1 and len(sc.items) == 1  # duplicados por título (mesmo entre feeds)
    assert sc.feed_status["C"]["ok"] is False and sc.feed_status["A"]["ok"] is True
    assert await sc.poll_once() == 0
    assert sc.snapshot()["items"][0]["assets"] == ["BTC"]


async def test_aggregate_sentiment_defaults_to_zero_and_averages():
    sc = GlobalMarketScanner(feeds={}, fetcher=None)
    assert sc.aggregate_sentiment("BTC") == 0.0
    sc.items = {
        "1": {"id": "1", "assets": ["BTC"], "sentiment": 1.0, "published": time.time()},
        "2": {"id": "2", "assets": ["BTC"], "sentiment": -0.5, "published": time.time()},
        "3": {"id": "3", "assets": ["BTC"], "sentiment": -1.0, "published": time.time() - 99999},  # fora da janela
        "4": {"id": "4", "assets": ["ETH"], "sentiment": 1.0, "published": time.time()},
    }
    assert sc.aggregate_sentiment("BTC") == 0.25


def test_calendar_example_labelled_and_pluggable():
    cal = get_economic_calendar()
    assert cal["source"] == "EXEMPLO" and cal["example"] is True
    assert all(e["forecast"] is None and e["previous"] is None for e in cal["events"])
    set_calendar_provider(lambda: [{"event": "CPI", "forecast": 3.0}])
    try:
        cal = get_economic_calendar()
        assert cal["source"] == "PROVEDOR" and cal["example"] is False
    finally:
        set_calendar_provider(None)


def test_calendar_never_imported_by_funnel():
    import funnel_engine
    assert not hasattr(funnel_engine, "get_economic_calendar")
