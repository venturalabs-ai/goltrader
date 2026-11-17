from .base import Readiness
from .stub import StubBroker


class IqoptionAdapter(StubBroker):
    name = "iqoption"
    display_name = "IQ Option"
    readiness = Readiness.STUB
    asset_classes = ('binary', 'forex', 'crypto')
    official_api = 'SEM API oficial pública. Apenas bibliotecas não oficiais (ex.: iqoptionapi)'
    notes = 'RISCO: viola ToS possível, conta pode ser bloqueada, API instável. Mantido desligado; só PAPER/PRACTICE; usar por conta e risco do usuário.'
