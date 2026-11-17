import hashlib
import hmac
import json
import time
import requests
import pandas as pd
from typing import Dict, Any
from core.config import settings

class BybitHybridConnector:
    def __init__(self):
        self.rest_url = settings.bybit_rest_url
        self.api_key = settings.bybit_api_key
        self.api_secret = settings.bybit_api_secret

    def fetch_klines(self, symbol: str = "BTCUSDT", interval: str = "1", limit: int = 1000, category: str = "spot") -> pd.DataFrame:
        url = f"{self.rest_url}/v5/market/kline"
        params = {"category": category, "symbol": symbol.upper(), "interval": str(interval), "limit": min(limit, 1000)}
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            raise RuntimeError(f"Bybit API error: {data.get('retMsg')}")
        df = pd.DataFrame(data["result"]["list"], columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = df[col].astype(float)
        df = df.sort_values("timestamp").drop_duplicates("timestamp").set_index("timestamp")
        return df.dropna(subset=["open", "high", "low", "close"])[lambda x: x["volume"] > 0]

    def place_order(self, symbol: str, direction: str, stake_usdt: float, expiry_candles: int = 1, dry_run: bool = True) -> Dict[str, Any]:
        side = "Buy" if direction.upper() == "CALL" else "Sell"
        meta = {
            "exchange": "Bybit-Testnet" if settings.bybit_testnet else "Bybit-Mainnet",
            "symbol": symbol.upper(), "direction": direction.upper(), "side": side,
            "stake_usdt": round(float(stake_usdt), 4), "expiry_candles": expiry_candles,
            "timestamp_utc": pd.Timestamp.utcnow().isoformat(),
        }
        if dry_run or not (self.api_key and self.api_secret):
            meta["mode"] = "DRY_RUN"
            meta["status"] = "FILLED_SIMULATED"
            meta["order_id"] = f"sim-{int(time.time() * 1000)}"
            return meta
        endpoint = f"{self.rest_url}/v5/order/create"
        body = {"category": "spot", "symbol": symbol.upper(), "side": side, "orderType": "Market", "qty": str(round(float(stake_usdt), 2)), "marketUnit": "quoteCoin"}
        payload_str = json.dumps(body)
        ts = str(int(time.time() * 1000))
        sig = hmac.new(self.api_secret.encode("utf-8"), f"{ts}{self.api_key}5000{payload_str}".encode("utf-8"), hashlib.sha256).hexdigest()
        headers = {"X-BAPI-API-KEY": self.api_key, "X-BAPI-SIGN": sig, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": "5000", "Content-Type": "application/json"}
        resp = requests.post(endpoint, headers=headers, data=payload_str, timeout=15)
        meta["mode"] = "LIVE_API"
        meta["exchange_response"] = resp.json()
        return meta
