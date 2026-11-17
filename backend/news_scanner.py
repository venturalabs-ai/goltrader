"""Scanner de notícias globais (RSS) + calendário econômico (provider plugável).

- RSS: CoinDesk, Cointelegraph, Investing BR; polling em tarefa de fundo (~2 min), com timeout e limite de tamanho.
- Parser XML seguro via defusedxml (bloqueia entidades/DTD maliciosos). Sem defusedxml, NÃO faz parsing.
- Sentimento: dicionário de palavras-chave PT/EN (heurística simples, NÃO é modelo de linguagem). Sem palavras = 0.0.
- Calendário: sem provedor configurado devolve apenas EXEMPLO rotulado (source='EXEMPLO'), nunca alimenta o funil.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp

try:  # parsing seguro obrigatório
    from defusedxml import ElementTree as SafeET
    from defusedxml.common import DefusedXmlException
except Exception:  # pragma: no cover
    SafeET = None  # type: ignore
    DefusedXmlException = Exception  # type: ignore

FEEDS: Dict[str, str] = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Investing BR": "https://br.investing.com/rss/news.rss",
}
MAX_BODY_BYTES = 2_000_000
USER_AGENT = "Goltrader-Pulse/1.0 (+https://github.com/venturalabs-ai/goltrader)"

POSITIVE = [
    "surge", "surges", "rally", "rallies", "soar", "soars", "jump", "jumps", "gain", "gains", "bullish", "record high",
    "all-time high", "ath", "approval", "approved", "adoption", "inflow", "inflows", "breakout", "recovers", "upgrade",
    "alta", "sobe", "dispara", "valoriza", "otimismo", "recorde", "aprovação", "aprovado", "entrada de capital", "rali",
]
NEGATIVE = [
    "plunge", "plunges", "crash", "crashes", "drop", "drops", "fall", "falls", "slump", "bearish", "hack", "hacked",
    "exploit", "lawsuit", "ban", "bans", "outflow", "outflows", "liquidation", "liquidations", "fraud", "sell-off",
    "selloff", "downgrade", "recession", "queda", "cai", "despenca", "desaba", "pessimismo", "calote", "fraude",
    "proibição", "saída de capital", "recessão",
]
ASSET_PATTERNS: Dict[str, str] = {
    "BTC": r"\b(bitcoin|btc)\b",
    "ETH": r"\b(ethereum|ether|eth)\b",
    "SOL": r"\b(solana|sol)\b",
    "XRP": r"\b(xrp|ripple)\b",
    "BNB": r"\b(bnb|binance coin)\b",
    "DOGE": r"\b(dogecoin|doge)\b",
    "XAU": r"\b(gold|ouro|xau)\b",
    "USD": r"\b(dollar|dólar|dolar|fed|federal reserve)\b",
}
_ASSET_RE = {k: re.compile(v, re.IGNORECASE) for k, v in ASSET_PATTERNS.items()}


def _compile(words: List[str]):
    return re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")(?!\w)", re.IGNORECASE)


_POS_RE = _compile(POSITIVE)
_NEG_RE = _compile(NEGATIVE)


def score_sentiment(text: str) -> float:
    """(pos − neg) / (pos + neg) em [-1, 1]; 0.0 quando não há palavra-chave."""
    pos = len(_POS_RE.findall(text))
    neg = len(_NEG_RE.findall(text))
    if pos + neg == 0:
        return 0.0
    return round((pos - neg) / (pos + neg), 3)


def detect_assets(text: str) -> List[str]:
    return [a for a, rx in _ASSET_RE.items() if rx.search(text)]


def item_id(title: str) -> str:
    return hashlib.sha1(title.strip().lower().encode("utf-8")).hexdigest()[:12]


def _safe_link(url: Optional[str]) -> str:
    if not url:
        return ""
    u = url.strip()
    return u if urlparse(u).scheme in ("http", "https") else ""


def _parse_date(raw: Optional[str]) -> float:
    if raw:
        try:
            return parsedate_to_datetime(raw).timestamp()
        except (TypeError, ValueError):
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
    return time.time()


def parse_rss(xml_bytes: bytes, source: str) -> List[Dict]:
    """RSS 2.0 / Atom mínimo -> itens normalizados. Levanta ValueError em XML inseguro/inválido."""
    if SafeET is None:
        raise ValueError("defusedxml ausente: parsing RSS desabilitado por segurança")
    try:
        root = SafeET.fromstring(xml_bytes)
    except DefusedXmlException as e:
        raise ValueError(f"XML rejeitado por segurança: {e}")
    except Exception as e:
        raise ValueError(f"XML inválido: {e}")

    out: List[Dict] = []
    for node in list(root.iter("item")) + [e for e in root.iter() if e.tag.endswith("}entry")]:
        def txt(name: str) -> str:
            for ch in node:
                if ch.tag == name or ch.tag.endswith("}" + name):
                    return "".join(ch.itertext()).strip()
            return ""
        title = re.sub(r"<[^>]+>", "", txt("title")).strip()
        if not title:
            continue
        link = txt("link")
        if not link:
            for ch in node:
                if ch.tag.endswith("link") and ch.get("href"):
                    link = ch.get("href", "")
        desc = re.sub(r"<[^>]+>", " ", txt("description") or txt("summary"))[:400]
        published = _parse_date(txt("pubDate") or txt("published") or txt("updated"))
        text = f"{title}. {desc}"
        out.append({
            "id": item_id(title), "title": title, "source": source, "link": _safe_link(link),
            "published": int(published), "sentiment": score_sentiment(text), "assets": detect_assets(text),
        })
    return out


Fetcher = Callable[[str], Awaitable[bytes]]


async def default_fetcher(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": USER_AGENT}) as s:
        async with s.get(url) as r:
            r.raise_for_status()
            body = await r.content.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                raise ValueError("feed maior que o limite")
            return body


class GlobalMarketScanner:
    def __init__(self, feeds: Optional[Dict[str, str]] = None, fetcher: Optional[Fetcher] = None, max_items: int = 120):
        self.feeds = feeds or dict(FEEDS)
        self.fetcher = fetcher or default_fetcher
        self.max_items = max_items
        self.items: Dict[str, Dict] = {}
        self.feed_status: Dict[str, Dict] = {n: {"ok": None, "error": None, "updated_at": None} for n in self.feeds}

    async def poll_once(self) -> int:
        """Busca todos os feeds em paralelo; falha de um feed não derruba os outros. Retorna itens novos."""
        async def one(name: str, url: str) -> int:
            try:
                body = await asyncio.wait_for(self.fetcher(url), timeout=15)
                new = 0
                for it in parse_rss(body, name):
                    if it["id"] not in self.items:
                        self.items[it["id"]] = it
                        new += 1
                self.feed_status[name] = {"ok": True, "error": None, "updated_at": int(time.time())}
                return new
            except Exception as e:  # rede/parse: registra e segue
                self.feed_status[name] = {"ok": False, "error": type(e).__name__ + ": " + str(e)[:120],
                                          "updated_at": int(time.time())}
                return 0

        counts = await asyncio.gather(*(one(n, u) for n, u in self.feeds.items()))
        if len(self.items) > self.max_items:
            keep = sorted(self.items.values(), key=lambda x: x["published"], reverse=True)[: self.max_items]
            self.items = {i["id"]: i for i in keep}
        return sum(counts)

    async def run(self, interval_s: float = 120.0):
        while True:
            await self.poll_once()
            await asyncio.sleep(interval_s)

    def aggregate_sentiment(self, asset: str = "BTC", window_min: int = 240) -> float:
        """Média do sentimento das notícias recentes do ativo; 0.0 se não houver (nada inventado)."""
        cutoff = time.time() - window_min * 60
        vals = [i["sentiment"] for i in self.items.values() if asset in i["assets"] and i["published"] >= cutoff]
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    def snapshot(self, limit: int = 15) -> Dict:
        items = sorted(self.items.values(), key=lambda x: x["published"], reverse=True)[:limit]
        return {"items": items, "feeds": self.feed_status, "source": "RSS", "count": len(self.items)}


# ------------------------------------------------------------------ calendário econômico
CalendarProvider = Callable[[], List[Dict]]
_calendar_provider: Optional[CalendarProvider] = None


def set_calendar_provider(provider: Optional[CalendarProvider]) -> None:
    """Hook: registre uma função que devolva eventos reais [{time, country, event, impact, previous, forecast, actual}]."""
    global _calendar_provider
    _calendar_provider = provider


def get_economic_calendar() -> Dict:
    """Provider real se registrado; senão APENAS exemplo rotulado (sem valores inventados). Nunca usado pelo funil."""
    if _calendar_provider is not None:
        try:
            return {"source": "PROVEDOR", "example": False, "events": list(_calendar_provider())}
        except Exception as e:
            return {"source": "ERRO", "example": False, "events": [], "error": type(e).__name__}
    examples = [
        ("EUA", "CPI (inflação ao consumidor)", "alto"),
        ("EUA", "Decisão de juros do FOMC", "alto"),
        ("EUA", "Payroll (NFP)", "alto"),
        ("BR", "IPCA", "médio"),
        ("BR", "Decisão de juros do Copom", "alto"),
    ]
    return {
        "source": "EXEMPLO",
        "example": True,
        "note": "dados de exemplo — sem datas/valores reais; conecte um provedor via set_calendar_provider()",
        "events": [
            {"time": None, "country": c, "event": e, "impact": i, "previous": None, "forecast": None, "actual": None}
            for c, e, i in examples
        ],
    }
