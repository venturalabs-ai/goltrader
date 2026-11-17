from .base import Readiness
from .stub import StubBroker


class OkxAdapter(StubBroker):
    name = "okx"
    display_name = "OKX"
    readiness = Readiness.STUB
    asset_classes = ('crypto',)
    official_api = 'https://www.okx.com/docs-v5/ (ccxt: okx)'
    notes = 'Demo trading via header x-simulated-trading: 1.'
