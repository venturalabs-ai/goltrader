from .base import Readiness
from .ccxt_base import CcxtAdapter


class BinanceAdapter(CcxtAdapter):
    name = "binance"
    display_name = "Binance"
    readiness = Readiness.PARTIAL
    asset_classes = ("crypto",)
    ccxt_id = "binance"
    credential_env = {"apiKey": "BINANCE_API_KEY", "secret": "BINANCE_API_SECRET"}
    official_api = "https://developers.binance.com/ (ccxt: binance)"
    notes = "Via ccxt (spot por padrão); testnet via sandbox do ccxt. Desligado por padrão. Verificar restrições regulatórias no Brasil/CVM."
