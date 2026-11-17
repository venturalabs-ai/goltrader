from .base import Readiness
from .ccxt_base import CcxtAdapter


class KrakenAdapter(CcxtAdapter):
    name = "kraken"
    display_name = "Kraken"
    readiness = Readiness.PARTIAL
    asset_classes = ("crypto",)
    ccxt_id = "kraken"
    supports_testnet = False
    credential_env = {"apiKey": "KRAKEN_API_KEY", "secret": "KRAKEN_API_SECRET"}
    official_api = "https://docs.kraken.com/ (ccxt: kraken; futures demo: demo-futures.kraken.com)"
    notes = "Spot via ccxt (sem sandbox spot): só PAPER ou LIVE (com travas). Futures demo usa outra API/chaves (não incluído). Desligado por padrão."
