from .base import Readiness
from .stub import StubBroker


class BinanceAdapter(StubBroker):
    name = "binance"
    display_name = "Binance"
    readiness = Readiness.STUB
    asset_classes = ('crypto',)
    official_api = 'https://developers.binance.com/ (ccxt: binance)'
    notes = 'Spot/Futures; testnet em testnet.binance.vision e testnet.binancefuture.com. Verificar restrições regulatórias no Brasil/CVM.'
