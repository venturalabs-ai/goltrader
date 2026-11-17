"""Cliente Bybit V5 (pybit) para o painel. Testnet e Real separados."""
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional

import pandas as pd
from pybit.unified_trading import HTTP

from dashboard_components import keys
from execution.bybit_hybrid_connector import BybitHybridConnector

HOSTS = {keys.TESTNET: "https://api-testnet.bybit.com", keys.REAL: "https://api.bybit.com"}


class BybitError(RuntimeError):
    pass


def fetch_candles(symbol: str, interval: str, category: str, source_mode: str, limit: int = 1000) -> pd.DataFrame:
    """Reusa o fetch_klines do motor (execution.bybit_hybrid_connector), escolhendo o host."""
    conn = BybitHybridConnector()
    conn.rest_url = HOSTS[source_mode]
    conn.api_key = conn.api_secret = ""  # dados publicos: nao precisa de chave
    return conn.fetch_klines(symbol=symbol, interval=interval, limit=limit, category=category)


class BybitClient:
    def __init__(self, mode: str):
        self.mode = mode
        k, s, self.key_source = keys.resolve(mode)
        self.authenticated = bool(k and s)
        self.http = HTTP(testnet=(mode == keys.TESTNET), api_key=k or None, api_secret=s or None,
                         recv_window=10000, timeout=15, max_retries=1, log_requests=False)

    # ---------- helpers ----------
    def _call(self, fn_name: str, **kw) -> Dict[str, Any]:
        try:
            resp = getattr(self.http, fn_name)(**kw)
        except Exception as e:  # pybit levanta InvalidRequestError / FailedRequestError
            raise BybitError(keys.scrub(e)) from None
        if resp.get("retCode") not in (0, None):
            raise BybitError(keys.scrub(f"{resp.get('retCode')}: {resp.get('retMsg')}"))
        return resp.get("result", {})

    def _need_auth(self):
        if not self.authenticated:
            raise BybitError("chaves da conta real não configuradas" if self.mode == keys.REAL
                             else "chaves da testnet não configuradas")

    # ---------- publico ----------
    def server_time(self) -> int:
        return int(self._call("get_server_time").get("timeSecond", 0))

    def ticker_price(self, category: str, symbol: str) -> float:
        lst = self._call("get_tickers", category=category, symbol=symbol).get("list", [])
        if not lst:
            raise BybitError(f"Ticker {symbol} não encontrado em {category}")
        return float(lst[0]["lastPrice"])

    def instrument(self, category: str, symbol: str) -> Dict[str, Any]:
        lst = self._call("get_instruments_info", category=category, symbol=symbol).get("list", [])
        if not lst:
            raise BybitError(f"Instrumento {symbol} não existe em {category} ({self.mode})")
        i = lst[0]
        lot, pf = i.get("lotSizeFilter", {}), i.get("priceFilter", {})
        step = lot.get("qtyStep") or lot.get("basePrecision") or "0.000001"
        return {"qty_step": step, "min_qty": float(lot.get("minOrderQty", 0) or 0),
                "min_notional": float(lot.get("minNotionalValue") or lot.get("minOrderAmt") or 0),
                "tick": pf.get("tickSize", "0.01")}

    # ---------- conta ----------
    def wallet(self) -> Dict[str, Any]:
        self._need_auth()
        last_err = None
        for acct in ("UNIFIED", "CONTRACT", "SPOT"):
            try:
                lst = self._call("get_wallet_balance", accountType=acct).get("list", [])
            except BybitError as e:
                last_err = e
                continue
            if lst:
                w = lst[0]
                coins = [{"moeda": c.get("coin"), "saldo": _f(c.get("walletBalance")), "equity": _f(c.get("equity")),
                          "usd": _f(c.get("usdValue")), "pnl_aberto": _f(c.get("unrealisedPnl"))}
                         for c in w.get("coin", []) if _f(c.get("walletBalance")) or _f(c.get("equity"))]
                usdt = next((c for c in w.get("coin", []) if c.get("coin") == "USDT"), {})
                avail = _f(w.get("totalAvailableBalance")) or _f(usdt.get("availableToWithdraw")) or _f(usdt.get("walletBalance"))
                return {"account_type": acct, "total_equity": _f(w.get("totalEquity")) or _f(usdt.get("equity")),
                        "available": avail, "coins": coins}
        raise last_err or BybitError("Carteira vazia")

    def positions(self, category: str = "linear") -> List[Dict[str, Any]]:
        self._need_auth()
        if category != "linear":
            return []
        lst = self._call("get_positions", category="linear", settleCoin="USDT").get("list", [])
        return [p for p in lst if _f(p.get("size")) > 0]

    def open_orders(self, category: str) -> List[Dict[str, Any]]:
        self._need_auth()
        kw = {"category": category}
        if category == "linear":
            kw["settleCoin"] = "USDT"
        return self._call("get_open_orders", **kw).get("list", [])

    def order_history(self, category: str, limit: int = 50) -> List[Dict[str, Any]]:
        self._need_auth()
        return self._call("get_order_history", category=category, limit=limit).get("list", [])

    def closed_pnl_today(self) -> float:
        """Soma do PnL realizado (linear) desde 00:00 UTC de hoje."""
        self._need_auth()
        start = int(pd.Timestamp.now(tz="UTC").normalize().timestamp() * 1000)
        total, cursor = 0.0, None
        for _ in range(10):
            kw = {"category": "linear", "startTime": start, "limit": 100}
            if cursor:
                kw["cursor"] = cursor
            r = self._call("get_closed_pnl", **kw)
            total += sum(_f(x.get("closedPnl")) for x in r.get("list", []))
            cursor = r.get("nextPageCursor")
            if not cursor:
                break
        return total

    def last_closed_pnl(self, symbol: str) -> Optional[float]:
        self._need_auth()
        lst = self._call("get_closed_pnl", category="linear", symbol=symbol, limit=1).get("list", [])
        return _f(lst[0].get("closedPnl")) if lst else None

    # ---------- ordens ----------
    def set_leverage(self, symbol: str, leverage: float):
        self._need_auth()
        try:
            self._call("set_leverage", category="linear", symbol=symbol, buyLeverage=str(leverage), sellLeverage=str(leverage))
        except BybitError as e:
            if "110043" not in str(e):  # leverage not modified
                raise

    def place_order(self, category: str, symbol: str, side: str, qty: str, order_type: str = "Market",
                    price: Optional[str] = None, take_profit: Optional[str] = None, stop_loss: Optional[str] = None,
                    reduce_only: bool = False) -> Dict[str, Any]:
        self._need_auth()
        body = {"category": category, "symbol": symbol, "side": side, "orderType": order_type, "qty": qty,
                "orderLinkId": f"gt-{int(time.time() * 1000)}"}
        if category == "spot" and order_type == "Market":
            body["marketUnit"] = "baseCoin"
        if order_type == "Limit":
            body["price"] = price
            body["timeInForce"] = "GTC"
        if take_profit:
            body["takeProfit"] = take_profit
        if stop_loss:
            body["stopLoss"] = stop_loss
        if reduce_only and category == "linear":
            body["reduceOnly"] = True
        return self._call("place_order", **body)

    def cancel_order(self, category: str, symbol: str, order_id: str) -> Dict[str, Any]:
        self._need_auth()
        return self._call("cancel_order", category=category, symbol=symbol, orderId=order_id)

    def cancel_all(self, category: str) -> Dict[str, Any]:
        self._need_auth()
        kw = {"category": category}
        if category == "linear":
            kw["settleCoin"] = "USDT"
        return self._call("cancel_all_orders", **kw)

    def close_position(self, symbol: str, side: str, size: str) -> Dict[str, Any]:
        close_side = "Sell" if side == "Buy" else "Buy"
        return self.place_order("linear", symbol, close_side, size, "Market", reduce_only=True)


def _f(x) -> float:
    try:
        return float(x) if x not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def round_step(value: float, step: str) -> str:
    d = Decimal(str(step))
    q = (Decimal(str(value)) / d).to_integral_value(rounding=ROUND_DOWN) * d
    return format(q.normalize() if q != 0 else Decimal(0), "f")


def qty_from_stake(stake_usdt: float, price: float, leverage: float, inst: Dict[str, Any], category: str) -> str:
    notional = stake_usdt * (leverage if category == "linear" else 1.0)
    return round_step(notional / price, inst["qty_step"])


# ---------------- SPOT helpers (adicionados para o autotrader spot)
def _spot_coin_free(self, coin: str) -> float:
    """Saldo livre (walletBalance - locked) de uma moeda na conta UNIFIED."""
    self._need_auth()
    lst = self._call("get_wallet_balance", accountType="UNIFIED", coin=coin).get("list", [])
    for c in (lst[0].get("coin", []) if lst else []):
        if c.get("coin") == coin:
            return max(0.0, _f(c.get("walletBalance")) - _f(c.get("locked")))
    return 0.0


def _spot_fills(self, symbol: str, order_id: str) -> dict:
    """Soma das execucoes de uma ordem spot: qty, valor em USDT, taxa e moeda da taxa."""
    self._need_auth()
    lst = self._call("get_executions", category="spot", symbol=symbol, orderId=order_id, limit=100).get("list", [])
    qty = sum(_f(x.get("execQty")) for x in lst)
    val = sum(_f(x.get("execValue")) for x in lst)
    fees = {}
    for x in lst:
        fc = x.get("feeCurrency") or ""
        fees[fc] = fees.get(fc, 0.0) + _f(x.get("execFee"))
    return {"qty": qty, "value": val, "avg": (val / qty) if qty else 0.0, "fees": fees, "n": len(lst)}


BybitClient.coin_free = _spot_coin_free
BybitClient.spot_fills = _spot_fills


def round_down(value: float, step: str) -> str:
    return round_step(value, step)
