from abc import ABC, abstractmethod
import pandas as pd
from typing import Dict, Any, Optional

class BaseBinaryStrategy(ABC):
    name: str = "base_strategy"
    description: str = ""

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        self.params = {**self.default_params(), **(params or {})}

    @classmethod
    @abstractmethod
    def default_params(cls) -> Dict[str, Any]:
        pass

    @classmethod
    def optuna_space(cls, trial) -> Dict[str, Any]:
        return cls.default_params()

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        pass
