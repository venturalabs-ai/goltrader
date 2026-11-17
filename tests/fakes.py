"""Fakes de Bybit (REST) e WebSocket para testes sem rede."""
import json
from urllib.parse import parse_qs, urlparse


class FakeResp:
    def __init__(self, j, status=200, headers=None):
        self._j, self.status_code, self.headers = j, status, headers or {}

    def json(self):
        if isinstance(self._j, Exception):
            raise self._j
        return self._j


def ok(result=None, **extra):
    return {"retCode": 0, "retMsg": "OK", "result": result if result is not None else {}, **extra}


def err(code, msg="erro"):
    return {"retCode": code, "retMsg": msg, "result": {}}


INSTRUMENTS = {
    "BTCUSDT": {"symbol": "BTCUSDT", "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "100"},
                "priceFilter": {"tickSize": "0.5"}},
    "ETHUSDT": {"symbol": "ETHUSDT", "lotSizeFilter": {"qtyStep": "0.01", "minOrderQty": "0.01", "maxOrderQty": "1000"},
                "priceFilter": {"tickSize": "0.01"}},
}


class FakeBybit:
    """Sessão `requests`-like que simula a Bybit. `script[(METHOD,path)]` = lista de respostas/exceções consumidas em ordem."""

    def __init__(self, last_price=60000.0):
        self.calls = []
        self.script = {}
        self.orders = {}          # link -> order dict
        self.history = {}
        self.positions = []       # dicts da Bybit
        self.total_equity = "10000"
        self.coins = [{"coin": "USDT", "equity": "10000", "availableToWithdraw": "10000"}]
        self.executions = []
        self.last_price = last_price
        self.cancelled_all = 0

    def request(self, method, url, headers=None, data=None, timeout=None):
        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        body = json.loads(data) if data else None
        self.calls.append({"method": method, "path": u.path, "query": q, "body": body, "headers": headers or {}})
        key = (method, u.path)
        if self.script.get(key):
            r = self.script[key].pop(0)
            if isinstance(r, Exception):
                raise r
            return r if isinstance(r, FakeResp) else FakeResp(r)
        return FakeResp(self._default(method, u.path, q, body))

    def _default(self, method, path, q, body):
        if path == "/v5/market/time":
            return ok({"timeSecond": "1700000000"}, time=1700000000000)
        if path == "/v5/market/tickers":
            return ok({"list": [{"symbol": q.get("symbol"), "lastPrice": str(self.last_price)}]})
        if path == "/v5/market/instruments-info":
            return ok({"list": [INSTRUMENTS[q["symbol"]]] if q.get("symbol") in INSTRUMENTS else []})
        if path == "/v5/order/create":
            link = body["orderLinkId"]
            if link in self.orders:
                return err(110072, "OrderLinkedID is duplicate")
            o = {**body, "orderId": f"oid-{len(self.orders)+1}", "orderStatus": "New", "cumExecQty": "0", "avgPrice": "0"}
            self.orders[link] = o
            return ok({"orderId": o["orderId"], "orderLinkId": link})
        if path == "/v5/order/realtime":
            link = q.get("orderLinkId")
            lst = [o for o in self.orders.values() if o["category"] == q["category"] and o["orderStatus"] in ("New", "PartiallyFilled")]
            if link:
                lst = [o for o in lst if o["orderLinkId"] == link]
            return ok({"list": lst, "nextPageCursor": ""})
        if path == "/v5/order/history":
            link = q.get("orderLinkId")
            return ok({"list": [o for o in self.history.values() if o["orderLinkId"] == link]})
        if path == "/v5/order/cancel-all":
            self.cancelled_all += 1
            for o in self.orders.values():
                if o["orderStatus"] == "New":
                    o["orderStatus"] = "Cancelled"
            return ok({"list": []})
        if path == "/v5/position/list":
            return ok({"list": self.positions, "nextPageCursor": ""})
        if path == "/v5/account/wallet-balance":
            return ok({"list": [{"totalEquity": self.total_equity, "coin": self.coins}]})
        if path == "/v5/execution/list":
            return ok({"list": self.executions})
        return err(404, f"rota fake não prevista: {path}")


class FakeWS:
    """WebSocket falso. `incoming` = lista de strings JSON (ou Exception) entregues por recv() na ordem."""

    def __init__(self, incoming, auto_acks=True):
        self.sent = []
        self.incoming = list(incoming)
        self.closed = False
        self.auto_acks = auto_acks

    async def send(self, msg):
        self.sent.append(json.loads(msg))
        if self.auto_acks:
            op = json.loads(msg).get("op")
            if op in ("auth", "subscribe"):
                self.incoming.insert(0, json.dumps({"success": True, "op": op, "ret_msg": ""}))

    async def recv(self):
        import asyncio
        while not self.incoming:
            await asyncio.sleep(3600)
        m = self.incoming.pop(0)
        if isinstance(m, Exception):
            raise m
        return m

    async def close(self):
        self.closed = True
