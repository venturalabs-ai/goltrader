"""Cliente assíncrono Bybit V5 (REST assinado + WebSocket público).

Origem: código colado por Wemerson (t313u). Alterações: credenciais/testnet injetáveis (testes), `set_leverage`,
`cancel_all`, helpers públicos (tickers, klines), tópicos kline no stream público, orderLinkId nas ordens
(exigido em opções), marketUnit=baseCoin em spot Market.
Testnet por padrão (BYBIT_TESTNET=true). Chaves só por variáveis de ambiente — nunca versionar.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Awaitable, Callable, Dict, List, Optional

import aiohttp
import websockets


class BybitV5AsyncClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: Optional[bool] = None,
    ):
        self.api_key = os.getenv("BYBIT_API_KEY", "") if api_key is None else api_key
        self.api_secret = os.getenv("BYBIT_API_SECRET", "") if api_secret is None else api_secret
        self.testnet = (os.getenv("BYBIT_TESTNET", "true").lower() == "true") if testnet is None else testnet
        self.recv_window = "5000"

        if self.testnet:
            self.base_url = "https://api-testnet.bybit.com"
            self.ws_public_url = "wss://stream-testnet.bybit.com/v5/public/linear"
        else:
            self.base_url = "https://api.bybit.com"
            self.ws_public_url = "wss://stream.bybit.com/v5/public/linear"

        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=5)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _sign_payload(self, payload_str: str) -> Dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        param_str = f"{timestamp}{self.api_key}{self.recv_window}{payload_str}"
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            param_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": self.recv_window,
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, payload: Dict) -> Dict:
        if not self.has_credentials:
            return {"retCode": -1, "retMsg": "credenciais Bybit ausentes (BYBIT_API_KEY/BYBIT_API_SECRET)"}
        body_str = json.dumps(payload, separators=(",", ":"))
        headers = self._sign_payload(body_str)
        session = await self.get_session()
        t0 = time.perf_counter()
        async with session.post(f"{self.base_url}{path}", data=body_str, headers=headers) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = {"retCode": -2, "retMsg": f"resposta não-JSON (HTTP {resp.status})"}
            data["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            return data

    async def place_order(
        self,
        category: str,  # "linear" | "spot" | "option"
        symbol: str,
        side: str,  # "Buy" | "Sell"
        qty: str,
        order_type: str = "Market",
        price: Optional[str] = None,
        take_profit: Optional[str] = None,
        stop_loss: Optional[str] = None,
    ) -> Dict:
        """Envia ordem assíncrona individual na Bybit V5 sem bloquear o event loop."""
        payload: Dict[str, str] = {
            "category": category,
            "symbol": symbol,
            "side": side.capitalize(),
            "orderType": order_type,
            "qty": str(qty),
            "timeInForce": "IOC" if order_type == "Market" else "GTC",
            "orderLinkId": f"gt-{uuid.uuid4().hex[:24]}",
        }
        if category == "spot" and order_type == "Market":
            payload["marketUnit"] = "baseCoin"  # qty em moeda base (padrão da Bybit seria cotação em compras)
        if price:
            payload["price"] = str(price)
        if take_profit:
            payload["takeProfit"] = str(take_profit)
        if stop_loss:
            payload["stopLoss"] = str(stop_loss)
        return await self._post("/v5/order/create", payload)

    async def place_batch_orders(self, category: str, orders: List[Dict]) -> Dict:
        """Até 20 ordens em uma chamada (/v5/order/create-batch)."""
        return await self._post("/v5/order/create-batch", {"category": category, "request": orders[:20]})

    async def set_leverage(self, symbol: str, leverage: int) -> Dict:
        lev = str(int(leverage))
        return await self._post(
            "/v5/position/set-leverage",
            {"category": "linear", "symbol": symbol, "buyLeverage": lev, "sellLeverage": lev},
        )

    async def cancel_all(self, category: str = "linear", settle_coin: str = "USDT") -> Dict:
        return await self._post("/v5/order/cancel-all", {"category": category, "settleCoin": settle_coin})

    # ---------------- endpoints públicos (sem chaves) ----------------
    async def _get_public(self, path: str, params: Dict[str, str]) -> Dict:
        session = await self.get_session()
        async with session.get(f"{self.base_url}{path}", params=params) as resp:
            return await resp.json(content_type=None)

    async def fetch_option_tickers(self, base_coin: str = "BTC") -> List[Dict]:
        """Grade completa de opções (Greeks, IV, OI, Mark Price) — API pública."""
        res = await self._get_public("/v5/market/tickers", {"category": "option", "baseCoin": base_coin})
        return res.get("result", {}).get("list", [])

    async def fetch_linear_tickers(self) -> List[Dict]:
        res = await self._get_public("/v5/market/tickers", {"category": "linear"})
        return res.get("result", {}).get("list", [])

    async def fetch_klines(self, symbol: str, interval: str = "1", limit: int = 200) -> List[Dict]:
        """Velas históricas, mais antigas primeiro. Formato: {time,open,high,low,close,volume}."""
        res = await self._get_public(
            "/v5/market/kline",
            {"category": "linear", "symbol": symbol, "interval": interval, "limit": str(limit)},
        )
        rows = res.get("result", {}).get("list", [])
        out = [
            {
                "time": int(r[0]) // 1000, "open": float(r[1]), "high": float(r[2]),
                "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]),
            }
            for r in rows
        ]
        out.sort(key=lambda c: c["time"])
        return out

    async def stream_public_orderbook(
        self,
        symbols: List[str],
        callback: Callable[[Dict], Awaitable[None]],
        kline_interval: str = "1",
        depth: int = 200,
    ):
        """WebSocket público com reconexão: Book L2, Trades, Liquidações e velas (kline)."""
        topics: List[str] = []
        for s in symbols:
            topics.extend([
                f"orderbook.{depth}.{s}",
                f"publicTrade.{s}",
                f"allLiquidation.{s}",
                f"kline.{kline_interval}.{s}",
            ])
        while True:
            try:
                async with websockets.connect(self.ws_public_url, ping_interval=20) as ws:
                    await ws.send(json.dumps({"op": "subscribe", "args": topics}))
                    async for msg in ws:
                        await callback(json.loads(msg))
            except Exception:
                await asyncio.sleep(2)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
