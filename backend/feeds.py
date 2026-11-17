"""Fontes de dados do Pulse: SimulatedFeed (SINTÉTICO) e BybitFeed (WS/REST públicos, sem chaves).

Interface comum:  await feed.start(); await feed.update(); snap = feed.snapshot(); await feed.stop()
O campo `data_source` do snapshot é 'SIMULADO' ou 'BYBIT' e é repassado ao payload/UI.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import asdict
from typing import Deque, Dict, List, Optional

import numpy as np

from config import env_float
from funnel_engine import classify_buildup
from models import BookWall, DerivativesPulse
from options_engine import chain_from_bybit, spot_from_bybit_tickers, synthetic_chain

SYMBOL = "BTCUSDT"


def compute_book_wall(symbol: str, bids: Dict[float, float], asks: Dict[float, float], max_range_pct: float = 0.5) -> Optional[Dict]:
    """Muros do book dentro de ±max_range_pct do mid, limitado à profundidade disponível (range_pct real)."""
    if not bids or not asks:
        return None
    best_bid, best_ask = max(bids), min(asks)
    mid = (best_bid + best_ask) / 2
    avail = min(mid - min(bids), max(asks) - mid) / mid * 100  # cobertura real do book recebido
    rng = min(max_range_pct, avail)
    if rng <= 0:
        return None
    lo, hi = mid * (1 - rng / 100), mid * (1 + rng / 100)
    bid_levels = {p: p * s for p, s in bids.items() if p >= lo}
    ask_levels = {p: p * s for p, s in asks.items() if p <= hi}
    bid_usd, ask_usd = sum(bid_levels.values()), sum(ask_levels.values())
    if not bid_levels or not ask_levels or ask_usd <= 0:
        return None
    bp = max(bid_levels, key=bid_levels.get)
    ap = max(ask_levels, key=ask_levels.get)
    return asdict(BookWall(
        symbol=symbol, bid_vol_usd_m=round(bid_usd / 1e6, 3), ask_vol_usd_m=round(ask_usd / 1e6, 3),
        biggest_bid_price=bp, biggest_bid_size_usd_m=round(bid_levels[bp] / 1e6, 3),
        biggest_ask_price=ap, biggest_ask_size_usd_m=round(ask_levels[ap] / 1e6, 3),
        imbalance_ratio=round(bid_usd / ask_usd, 2), range_pct=round(rng, 4),
    ))


# ======================================================================= SIMULADO
class SimulatedFeed:
    data_source = "SIMULADO"

    def __init__(self, seed: Optional[int] = None):
        self.rng = np.random.default_rng(seed)
        self.price = 65400.0
        self.candles: List[Dict] = []
        t0 = (int(time.time()) // 60) * 60 - 60 * 60
        p = self.price
        for i in range(60):
            o = p
            c = o + self.rng.normal(0, 45)
            h = max(o, c) + abs(self.rng.normal(0, 25))
            l = min(o, c) - abs(self.rng.normal(0, 25))
            self.candles.append({"time": t0 + i * 60, "open": round(o, 2), "high": round(h, 2), "low": round(l, 2),
                                 "close": round(c, 2), "volume": round(float(self.rng.exponential(30)), 3)})
            p = c
        self.price = p
        self._bubbles: List[Dict] = []
        self._last_delta = 0.0
        self._oi_delta = 0.0
        self._liq = 0.0
        self._book: Optional[Dict] = None
        self._eth = 1822.0

    async def start(self): ...
    async def stop(self): ...

    async def update(self):
        rng = self.rng
        now_min = (int(time.time()) // 60) * 60
        last = self.candles[-1]
        if now_min > last["time"]:  # nova vela de 1m
            self.candles.append({"time": now_min, "open": last["close"], "high": last["close"], "low": last["close"],
                                 "close": last["close"], "volume": 0.0})
            self.candles = self.candles[-300:]
            last = self.candles[-1]
        delta = float(rng.normal(0, 18))
        new_close = round(last["close"] + delta, 2)
        last["close"] = new_close
        last["high"] = round(max(last["high"], new_close), 2)
        last["low"] = round(min(last["low"], new_close), 2)
        self.price, self._last_delta = new_close, delta

        size = round(float(rng.exponential(28000)), 2)
        whale_min = env_float("GOLTRADER_WHALE_USD", 85000)
        self._bubbles = [{
            "id": f"FL-{time.time_ns()}", "symbol": SYMBOL, "side": "BUY" if delta >= 0 else "SELL",
            "size_usd": size, "price": new_close, "is_whale": size >= whale_min,
            "venue": str(rng.choice(["Bybit Perp", "OKX Perp", "Binance", "IQ Digital"])), "time": time.strftime("%H:%M:%S"),
        }]
        last["volume"] = round(last["volume"] + size / new_close, 3)

        bid = 42.5 + float(rng.uniform(-4, 8))
        ask = 36.8 + float(rng.uniform(-5, 6))
        self._book = asdict(BookWall(
            symbol=SYMBOL, bid_vol_usd_m=round(bid, 2), ask_vol_usd_m=round(ask, 2),
            biggest_bid_price=round(new_close * 0.996, 2), biggest_bid_size_usd_m=3.85,
            biggest_ask_price=round(new_close * 1.004, 2), biggest_ask_size_usd_m=2.90,
            imbalance_ratio=round(bid / max(ask, 1), 2), range_pct=0.5,
        ))
        self._oi_delta = float(rng.uniform(-1.5, 2.2))
        self._liq = 180000.0 if abs(float(rng.normal(0, 1))) > 2.3 else 12000.0
        self._eth = round(self._eth + float(rng.normal(0, 2)), 2)

    def snapshot(self) -> Dict:
        bubbles, self._bubbles = self._bubbles, []
        btc = DerivativesPulse(
            symbol=SYMBOL, last_price=self.price, change_24h=round((self.price - 65000) / 65000 * 100, 2),
            open_interest_usd_m=2090.0, oi_change_pct=round(self._oi_delta, 2), oi_window_s=0, funding_rate_pct=0.0021,
            basis_pct=3.85, pcr_oi=None, max_pain=None, atm_iv=None,
            buildup_status=classify_buildup(self._last_delta, self._oi_delta),
        )
        eth = DerivativesPulse(
            symbol="ETHUSDT", last_price=self._eth, change_24h=0.42, open_interest_usd_m=1230.0, oi_change_pct=1.15,
            oi_window_s=0, funding_rate_pct=-0.0012, basis_pct=-2.10, pcr_oi=None, max_pain=None, atm_iv=None,
            buildup_status="LONG BUILDUP",
        )
        return {
            "data_source": self.data_source, "ready": True, "symbol": SYMBOL, "price": self.price,
            "candles": list(self.candles), "flow_bubbles": bubbles, "book_wall": self._book,
            "derivatives": [asdict(btc), asdict(eth)], "liquidation_usd": self._liq,
            "price_delta": self._last_delta, "oi_delta": self._oi_delta,
        }

    async def option_chain(self):
        return synthetic_chain(self.price), self.price, "SIMULADO"


# ======================================================================= BYBIT PÚBLICO
class BybitFeed:
    data_source = "BYBIT"

    def __init__(self, client, symbol: str = SYMBOL, pulse_symbols=("BTCUSDT", "ETHUSDT"), option_base: str = "BTC"):
        self.client = client  # BybitV5AsyncClient (mainnet público, sem chaves)
        self.symbol = symbol
        self.pulse_symbols = pulse_symbols
        self.option_base = option_base
        self.candles: List[Dict] = []
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self._bubbles: Deque[Dict] = deque(maxlen=60)
        self._liqs: Deque = deque(maxlen=500)  # (ts, usd)
        self._tickers: Dict[str, Dict] = {}
        self._oi_hist: Dict[str, Deque] = {s: deque(maxlen=400) for s in pulse_symbols}  # (ts, price, oi_usd)
        self.price = 0.0
        self._tasks: List[asyncio.Task] = []
        self.last_msg_ts = 0.0

    async def start(self):
        try:
            self.candles = (await self.client.fetch_klines(self.symbol, "1", 200))[-300:]
            if self.candles:
                self.price = self.candles[-1]["close"]
        except Exception:
            pass
        self._tasks = [
            asyncio.create_task(self.client.stream_public_orderbook([self.symbol], self.handle)),
            asyncio.create_task(self._ticker_loop()),
        ]

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.client.close()

    # -------- mensagens WS
    async def handle(self, msg: Dict):
        topic = msg.get("topic", "")
        data = msg.get("data")
        if not topic or data is None:
            return
        self.last_msg_ts = time.time()
        if topic.startswith("orderbook."):
            self._apply_book(msg.get("type"), data)
        elif topic.startswith("publicTrade."):
            self._on_trades(data)
        elif topic.startswith("kline."):
            self._on_kline(data)
        elif "iquidation" in topic:
            self._on_liq(data)

    def _apply_book(self, typ: Optional[str], d: Dict):
        if typ == "snapshot":
            self.bids.clear()
            self.asks.clear()
        for side, book in (("b", self.bids), ("a", self.asks)):
            for price, size in d.get(side, []):
                p, s = float(price), float(size)
                if s == 0:
                    book.pop(p, None)
                else:
                    book[p] = s

    def _on_trades(self, trades: List[Dict]):
        whale_min = env_float("GOLTRADER_WHALE_USD", 85000)
        bubble_min = env_float("GOLTRADER_BUBBLE_MIN_USD", 10000)
        for t in trades:
            p, v = float(t["p"]), float(t["v"])
            usd = p * v
            self.price = p
            if usd >= bubble_min:
                self._bubbles.append({
                    "id": f"FL-{t.get('i', time.time_ns())}", "symbol": t.get("s", self.symbol),
                    "side": "BUY" if t.get("S") == "Buy" else "SELL", "size_usd": round(usd, 2), "price": p,
                    "is_whale": usd >= whale_min, "venue": "Bybit Perp",
                    "time": time.strftime("%H:%M:%S", time.localtime(int(t.get("T", time.time() * 1000)) / 1000)),
                })

    def _on_kline(self, rows: List[Dict]):
        for k in rows:
            c = {"time": int(k["start"]) // 1000, "open": float(k["open"]), "high": float(k["high"]),
                 "low": float(k["low"]), "close": float(k["close"]), "volume": float(k.get("volume", 0) or 0)}
            self.price = c["close"]
            if self.candles and self.candles[-1]["time"] == c["time"]:
                self.candles[-1] = c
            elif not self.candles or c["time"] > self.candles[-1]["time"]:
                self.candles.append(c)
                self.candles = self.candles[-300:]

    def _on_liq(self, data):
        rows = data if isinstance(data, list) else [data]
        for r in rows:
            try:
                self._liqs.append((time.time(), float(r["p"]) * float(r["v"])))
            except (KeyError, ValueError, TypeError):
                continue

    # -------- REST (derivativos)
    async def _ticker_loop(self):
        while True:
            try:
                for t in await self.client.fetch_linear_tickers():
                    if t.get("symbol") in self.pulse_symbols:
                        self._tickers[t["symbol"]] = t
                        now = time.time()
                        oi = float(t.get("openInterestValue") or 0)
                        px = float(t.get("lastPrice") or 0)
                        if px > 0 and oi > 0:
                            self._oi_hist[t["symbol"]].append((now, px, oi))
            except Exception:
                pass
            await asyncio.sleep(5)

    def _pulse_row(self, sym: str) -> Optional[Dict]:
        t = self._tickers.get(sym)
        if not t:
            return None
        px = float(t.get("lastPrice") or 0)
        oi = float(t.get("openInterestValue") or 0)
        hist = self._oi_hist[sym]
        pdelta = odelta = 0.0
        oi_pct = win = None
        if len(hist) >= 2:
            ref = next((h for h in hist if time.time() - h[0] <= 300), hist[0])  # ~5 min
            if ref[1] > 0 and ref[2] > 0:
                pdelta, odelta = px - ref[1], oi - ref[2]
                oi_pct, win = round(odelta / ref[2] * 100, 3), int(hist[-1][0] - ref[0])
        idx, mark = float(t.get("indexPrice") or 0), float(t.get("markPrice") or 0)
        row = DerivativesPulse(
            symbol=sym, last_price=px,
            change_24h=round(float(t["price24hPcnt"]) * 100, 2) if t.get("price24hPcnt") not in (None, "") else None,
            open_interest_usd_m=round(oi / 1e6, 1) if oi else None, oi_change_pct=oi_pct, oi_window_s=win,
            funding_rate_pct=round(float(t["fundingRate"]) * 100, 4) if t.get("fundingRate") not in (None, "") else None,
            basis_pct=round((mark - idx) / idx * 100, 4) if idx and mark else None,
            pcr_oi=None, max_pain=None, atm_iv=None,
            buildup_status=classify_buildup(pdelta, odelta) if oi_pct is not None else "SEM DADOS",
        )
        return asdict(row)

    async def update(self):  # dados chegam por WS/REST em tarefas de fundo
        return None

    def snapshot(self) -> Dict:
        bubbles = list(self._bubbles)
        self._bubbles.clear()
        cutoff = time.time() - 60
        liq = sum(u for ts, u in self._liqs if ts >= cutoff)
        rows = [r for r in (self._pulse_row(s) for s in self.pulse_symbols) if r]
        book = compute_book_wall(self.symbol, self.bids, self.asks)
        fresh = time.time() - self.last_msg_ts < 15
        return {
            "data_source": self.data_source, "ready": bool(len(self.candles) >= 30 and book and fresh),
            "symbol": self.symbol, "price": self.price, "candles": list(self.candles), "flow_bubbles": bubbles,
            "book_wall": book, "derivatives": rows, "liquidation_usd": liq,
        }

    async def option_chain(self):
        tickers = await self.client.fetch_option_tickers(self.option_base)
        chain = chain_from_bybit(tickers)
        spot = spot_from_bybit_tickers(tickers) or self.price
        return chain, spot, "BYBIT"
