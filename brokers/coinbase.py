from .base import Readiness
from .ccxt_base import CcxtAdapter


class CoinbaseAdapter(CcxtAdapter):
    name = "coinbase"
    display_name = "Coinbase"
    readiness = Readiness.PARTIAL
    asset_classes = ("crypto",)
    ccxt_id = "coinbase"
    supports_testnet = False
    credential_env = {"apiKey": "COINBASE_API_KEY_NAME", "secret": "COINBASE_API_PRIVATE_KEY"}
    official_api = "https://docs.cdp.coinbase.com/advanced-trade/ (ccxt: coinbase)"
    notes = "Advanced Trade via ccxt. Sem sandbox utilizável: só PAPER ou LIVE (com todas as travas). Desligado por padrão."
