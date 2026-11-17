"""Loop único do motor: lê o feed, roda algoritmos, dispara o funil (paper se dados simulados) e monta o payload.

Um único loop global alimenta todos os clientes WS (o código original rodava o funil por conexão => ordens duplicadas).
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional

from config import data_source_name, env_bool, env_float
from connectors.bybit_async import BybitV5AsyncClient
from execution import execute_single_order, state
from feeds import BybitFeed, SimulatedFeed
from funnel_engine import detect_order_blocks, evaluate_inefficiency_funnel, inside_confirmed_ob, vwap_zscore
from models import SmartOrderRequest
from news_scanner import GlobalMarketScanner, get_economic_calendar
from options_engine import compute_options_analytics, iv_rv_premium, realized_vol

TICK_S = 0.8


class Engine:
    def __init__(self, feed=None, scanner: Optional[GlobalMarketScanner] = None):
        if feed is None:
            if data_source_name() == "BYBIT":
                feed = BybitFeed(BybitV5AsyncClient(api_key="", api_secret="", testnet=False))  # dados públicos mainnet
            else:
                feed = SimulatedFeed()
        self.feed = feed
        self.scanner = scanner or GlobalMarketScanner()
        state.data_source = feed.data_source
        self.latest: Optional[Dict] = None
        self.options_cache: Optional[Dict] = None
        self.options_error: Optional[str] = None
        self.last_funnel_ts = 0.0
        self.tasks = []

    # ---------------------------------------------------------------- ciclo de vida
    async def start(self):
        await self.feed.start()
        self.tasks = [
            asyncio.create_task(self._loop()),
            asyncio.create_task(self.scanner.run(120.0)),
            asyncio.create_task(self._options_loop()),
        ]

    async def stop(self):
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.feed.stop()

    async def _loop(self):
        while True:
            try:
                await self.tick()
            except Exception as e:  # nunca derrubar o loop
                self.latest = {**(self.latest or {}), "engine_error": f"{type(e).__name__}: {str(e)[:120]}"}
            await asyncio.sleep(TICK_S)

    async def _options_loop(self):
        while True:
            await self.refresh_options()
            await asyncio.sleep(env_float("GOLTRADER_OPTIONS_REFRESH_S", 30))

    async def refresh_options(self):
        try:
            chain, spot, source = await asyncio.wait_for(self.feed.option_chain(), timeout=20)
            self.options_cache = compute_options_analytics(chain, spot, source)
            self.options_error = None if self.options_cache else "cadeia de opções vazia"
        except Exception as e:
            self.options_error = f"{type(e).__name__}: {str(e)[:100]}"

    # ---------------------------------------------------------------- um tick
    async def tick(self) -> Dict:
        await self.feed.update()
        snap = self.feed.snapshot()
        if self.options_cache is None and self.options_error is None and snap["data_source"] == "SIMULADO":
            await self.refresh_options()

        candles = snap["candles"]
        price = snap["price"]
        obs = detect_order_blocks(snap["symbol"], candles) if candles else []
        z = vwap_zscore(candles)
        wall = snap["book_wall"]
        news_score = self.scanner.aggregate_sentiment("BTC")

        signal = None
        if snap["ready"] and z is not None and wall is not None:
            signal = evaluate_inefficiency_funnel(
                symbol=snap["symbol"], price=price, vwap_deviation_zscore=z, book_imbalance=wall["imbalance_ratio"],
                inside_confirmed_ob=inside_confirmed_ob(price, obs), news_sentiment_score=news_score,
                liquidation_spike_usd=snap["liquidation_usd"],
            )
        if signal:
            await self._maybe_fire(signal)

        # opções: junta vol realizada calculada das velas (dado real quando a fonte é real)
        options = None
        if self.options_cache:
            options = dict(self.options_cache)
            rv = realized_vol(candles)
            options["rv_pct"] = rv["rv_pct"] if rv else None
            options["rv_window_returns"] = rv["n_returns"] if rv else 0
            options["iv_rv_premium_pts"] = iv_rv_premium(options.get("atm_iv_pct"), options["rv_pct"])
            # enriquece a linha BTC do pulse
            for row in snap["derivatives"]:
                if row["symbol"] == snap["symbol"]:
                    row["pcr_oi"], row["max_pain"], row["atm_iv"] = options["pcr_oi"], options["max_pain"], options["atm_iv_pct"]
        elif self.options_error:
            options = {"source": snap["data_source"], "error": self.options_error}

        bubbles = snap["flow_bubbles"]
        biggest = max(bubbles, key=lambda b: b["size_usd"]) if bubbles else None
        self.latest = {
            "server_time": int(time.time() * 1000),
            "data_source": snap["data_source"],
            "simulated": snap["data_source"] == "SIMULADO",
            "feed_ready": snap["ready"],
            "analyzer_mode": state.analyzer_mode,
            "autonomous_funnel": state.autonomous_funnel,
            "kill_switch": state.kill_switch,
            "live_allowed": env_bool("GOLTRADER_ALLOW_LIVE", False),
            "limits": {"daily_pnl_usd": round(state.daily_pnl_usd, 2)},
            "candle": candles[-1] if candles else None,
            "history": candles[-120:],
            "flow_bubble": biggest,
            "flow_bubbles": bubbles,
            "book_wall": wall,
            "order_blocks": obs,
            "derivatives_pulse": snap["derivatives"],
            "funnel_signal": signal,
            "z_score": None if z is None else round(z, 2),
            "news_sentiment_btc": news_score,
            "active_positions": state.active_positions[:8],
            "options_analytics": options,
            "market_news": self.scanner.snapshot(),
            "economic_calendar": get_economic_calendar(),
        }
        return self.latest

    async def _maybe_fire(self, signal: Dict):
        """Funil autônomo: cooldown + guardas globais; com dados SIMULADOS vira paper (garantido em execute_single_order)."""
        if not state.autonomous_funnel or state.kill_switch:
            return
        cooldown = env_float("GOLTRADER_FUNNEL_COOLDOWN_S", 60)
        if time.time() - self.last_funnel_ts < cooldown:
            return
        self.last_funnel_ts = time.time()
        qty = env_float("GOLTRADER_FUNNEL_QTY", 0.001)
        legs = [SmartOrderRequest(symbol=signal["symbol"], venue="BYBIT_PERP", side=signal["direction"], amount=qty,
                                  leverage=min(5, int(env_float("GOLTRADER_MAX_LEVERAGE", 10))))]
        if env_bool("GOLTRADER_FUNNEL_IQ", False):
            legs.append(SmartOrderRequest(symbol=signal["symbol"], venue="IQ_BINARY", side=signal["direction"], amount=1.0))
        await asyncio.gather(*(execute_single_order(o, origin="FUNIL") for o in legs))
