from .base import Readiness
from .stub import StubBroker


class CoinbaseAdapter(StubBroker):
    name = "coinbase"
    display_name = "Coinbase"
    readiness = Readiness.STUB
    asset_classes = ('crypto',)
    official_api = 'https://docs.cdp.coinbase.com/advanced-trade/ (ccxt: coinbase)'
    notes = 'Advanced Trade API; sandbox limitado.'
