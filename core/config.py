import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

FREE_OPENCODE_MODELS = [
    "Beam Free", "Big Pickle", "GLM-5.3-Flash Free", "DeepSeek V4.1 Flash Free",
    "Nemotron 3.5 Lightning Free", "Nemotron 3.5 Super VL Free",
    "MiniMax M3.1 Flash Free", "MiMo-V2.6-Flash Free", "LongCat 2.5 Free",
    "Inkling 2 Small Free", "Flint 40B Free",
]

@dataclass
class Settings:
    bybit_testnet: bool = os.getenv("BYBIT_TESTNET", "true").lower() == "true"
    bybit_api_key: str = os.getenv("BYBIT_API_KEY", "")
    bybit_api_secret: str = os.getenv("BYBIT_API_SECRET", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://localhost:4000/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "sk-opencode-free")
    llm_model: str = os.getenv("LLM_MODEL", "DeepSeek V4.1 Flash Free")
    default_symbol: str = os.getenv("DEFAULT_SYMBOL", "BTCUSDT")
    default_interval: str = os.getenv("DEFAULT_INTERVAL", "1")
    default_payout: float = float(os.getenv("DEFAULT_PAYOUT", "0.85"))
    expiry_candles: int = int(os.getenv("EXPIRY_CANDLES", "1"))

    @property
    def bybit_rest_url(self) -> str:
        return "https://api-testnet.bybit.com" if self.bybit_testnet else "https://api.bybit.com"

settings = Settings()
