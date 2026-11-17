import numpy as np
import pandas as pd
import optuna
import mlflow
from typing import Dict, Any, Tuple, Type
from strategies.base import BaseBinaryStrategy
from validation.stats import evaluate_binary_outcomes
from core.hybrid_memory import HybridMemoryStore

optuna.logging.set_verbosity(optuna.logging.WARNING)

class HybridBinaryBacktestEngine:
    def __init__(self, payout: float = 0.85, expiry_candles: int = 1, memory: HybridMemoryStore = None):
        self.payout = payout
        self.expiry_candles = expiry_candles
        self.memory = memory or HybridMemoryStore()

    def vectorized_outcomes(self, df: pd.DataFrame, signals: pd.Series) -> np.ndarray:
        ent, exp = df["open"], df["close"].shift(-(self.expiry_candles - 1))
        valid = (signals != 0) & ent.notna() & exp.notna()
        s, e1, e2 = signals[valid].values, ent[valid].values, exp[valid].values
        return np.where(((s == 1) & (e2 > e1)) | ((s == -1) & (e2 < e1)), 1, 0)

    def walk_forward_optimize_and_validate(self, df: pd.DataFrame, strategy_cls: Type[BaseBinaryStrategy], symbol: str = "BTCUSDT", train_ratio: float = 0.70, n_trials: int = 15, n_strategies_compared: int = 10) -> Tuple[Dict[str, Any], np.ndarray, BaseBinaryStrategy]:
        split_idx = int(len(df) * train_ratio)
        df_train, df_oos = df.iloc[:split_idx], df.iloc[split_idx:]

        def objective(trial: optuna.Trial) -> float:
            strat = strategy_cls(params=strategy_cls.optuna_space(trial))
            out = self.vectorized_outcomes(df_train, strat.generate_signals(df_train))
            if len(out) < 10:
                return -1.0
            wr = float(np.mean(out == 1))
            return wr * self.payout - (1.0 - wr)

        study = optuna.create_study(study_name=f"goltrader_{symbol}_{strategy_cls.name}", storage=self.memory.optuna_storage_url, load_if_exists=True, direction="maximize")
        study.optimize(objective, n_trials=n_trials)

        best_params = {**strategy_cls.default_params(), **study.best_params}
        champion = strategy_cls(params=best_params)
        oos_outcomes = self.vectorized_outcomes(df_oos, champion.generate_signals(df_oos))
        metrics = evaluate_binary_outcomes(oos_outcomes, payout=self.payout, n_strategies_compared=n_strategies_compared)
        metrics["best_params"] = best_params

        try:
            mlflow.set_tracking_uri(self.memory.mlflow_tracking_uri)
            mlflow.set_experiment("Goltrader_10_Methodologies")
            with mlflow.start_run(run_name=f"{symbol}_{champion.name}"):
                mlflow.log_params(best_params)
                mlflow.log_metrics({"oos_trades": metrics["trades"], "oos_win_rate": metrics["win_rate"], "oos_ev": metrics["EV_por_trade"], "oos_p_value": metrics["p_value"]})
        except Exception:
            pass
        return metrics, oos_outcomes, champion
