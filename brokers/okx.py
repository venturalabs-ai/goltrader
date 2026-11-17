from .base import Readiness
from .ccxt_base import CcxtAdapter


class OkxAdapter(CcxtAdapter):
    name = "okx"
    display_name = "OKX"
    readiness = Readiness.PARTIAL
    asset_classes = ("crypto",)
    ccxt_id = "okx"
    credential_env = {"apiKey": "OKX_API_KEY", "secret": "OKX_API_SECRET", "password": "OKX_API_PASSPHRASE"}
    official_api = "https://www.okx.com/docs-v5/ (ccxt: okx)"
    notes = "Via ccxt; TESTNET = demo trading (sandbox do ccxt). Desligado por padrão."
