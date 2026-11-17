from .base import Readiness
from .stub import StubBroker


class IbkrAdapter(StubBroker):
    name = "ibkr"
    display_name = "Interactive Brokers"
    readiness = Readiness.STUB
    asset_classes = ('stocks', 'options', 'futures', 'forex')
    official_api = 'TWS API / IB Gateway (ib_async) ou Client Portal Web API'
    notes = 'Paper trading em porta separada (TWS 7497 / Gateway 4002). Inclui B3 via IBKR (acesso a mercados internacionais).'
