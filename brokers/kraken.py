from .base import Readiness
from .stub import StubBroker


class KrakenAdapter(StubBroker):
    name = "kraken"
    display_name = "Kraken"
    readiness = Readiness.STUB
    asset_classes = ('crypto',)
    official_api = 'https://docs.kraken.com/ (ccxt: kraken; futures: demo-futures.kraken.com)'
    notes = 'Spot e Futures têm APIs/chaves distintas.'
