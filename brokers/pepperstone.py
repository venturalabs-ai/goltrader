from .base import Readiness
from .stub import StubBroker


class PepperstoneAdapter(StubBroker):
    name = "pepperstone"
    display_name = "Pepperstone"
    readiness = Readiness.STUB
    asset_classes = ('forex', 'cfd')
    official_api = 'cTrader Open API (OAuth2) ou MetaTrader 5 (pacote MetaTrader5, só Windows)'
    notes = 'CFDs/FX. Conta demo disponível. MT5 exige terminal no Windows (notebook), não roda no Linux da VPS sem Wine.'
