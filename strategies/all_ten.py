import numpy as np
import pandas as pd
from typing import Dict, Any, List, Type
from strategies.base import BaseBinaryStrategy

def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/length, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/length, adjust=False).mean().replace(0, 1e-9)
    rs = gain / loss
    return 100.0 - (100.0 / (1.0 + rs))

def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/length, adjust=False).mean()

class M01RsiBollingerReversion(BaseBinaryStrategy):
    name = "M01_RSI_BB_Reversion"
    @classmethod
    def default_params(cls): return {"rsi_len": 14, "rsi_low": 28.0, "rsi_high": 72.0, "bb_len": 20, "bb_std": 2.0}
    @classmethod
    def optuna_space(cls, trial):
        return {"rsi_len": trial.suggest_int("rsi_len", 7, 21), "rsi_low": trial.suggest_float("rsi_low", 20.0, 35.0),
                "rsi_high": trial.suggest_float("rsi_high", 65.0, 80.0), "bb_len": trial.suggest_int("bb_len", 14, 30), "bb_std": trial.suggest_float("bb_std", 1.8, 2.5)}
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        p = self.params
        rsi = _rsi(df["close"], int(p["rsi_len"])).shift(1)
        sma = df["close"].rolling(int(p["bb_len"])).mean().shift(1)
        std = df["close"].rolling(int(p["bb_len"])).std().shift(1)
        c = df["close"].shift(1)
        sig = pd.Series(0, index=df.index, dtype=int)
        sig[(c < sma - p["bb_std"] * std) & (rsi < p["rsi_low"])] = 1
        sig[(c > sma + p["bb_std"] * std) & (rsi > p["rsi_high"])] = -1
        return sig

class M02PriceActionPinBarSR(BaseBinaryStrategy):
    name = "M02_PriceAction_PinBar_SR"
    @classmethod
    def default_params(cls): return {"sr_lookback": 20, "wick_ratio": 2.2}
    @classmethod
    def optuna_space(cls, trial): return {"sr_lookback": trial.suggest_int("sr_lookback", 10, 40), "wick_ratio": trial.suggest_float("wick_ratio", 1.6, 3.2)}
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        p = self.params
        lb = int(p["sr_lookback"])
        o, h, l, c = df["open"].shift(1), df["high"].shift(1), df["low"].shift(1), df["close"].shift(1)
        sup, res = df["low"].shift(2).rolling(lb).min(), df["high"].shift(2).rolling(lb).max()
        body = (c - o).abs().replace(0, 1e-9)
        sig = pd.Series(0, index=df.index, dtype=int)
        sig[(l <= sup) & ((np.minimum(o, c) - l) >= body * p["wick_ratio"]) & (c >= o)] = 1
        sig[(h >= res) & ((h - np.maximum(o, c)) >= body * p["wick_ratio"]) & (c <= o)] = -1
        return sig

class M03VolatilitySqueezeBreakout(BaseBinaryStrategy):
    name = "M03_Squeeze_Breakout"
    @classmethod
    def default_params(cls): return {"length": 20, "bb_std": 2.0, "kc_mult": 1.5}
    @classmethod
    def optuna_space(cls, trial): return {"length": trial.suggest_int("length", 14, 28), "bb_std": trial.suggest_float("bb_std", 1.8, 2.4), "kc_mult": trial.suggest_float("kc_mult", 1.2, 2.0)}
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        p = self.params
        n = int(p["length"])
        mid = df["close"].rolling(n).mean()
        sd = df["close"].rolling(n).std()
        atr = _atr(df["high"], df["low"], df["close"], n)
        sq_prev = ((mid - p["bb_std"]*sd).shift(2) > (mid - p["kc_mult"]*atr).shift(2)) & ((mid + p["bb_std"]*sd).shift(2) < (mid + p["kc_mult"]*atr).shift(2))
        rel_now = ((mid - p["bb_std"]*sd).shift(1) <= (mid - p["kc_mult"]*atr).shift(1)) | ((mid + p["bb_std"]*sd).shift(1) >= (mid + p["kc_mult"]*atr).shift(1))
        mom = (df["close"] - df["close"].shift(n)).shift(1)
        sig = pd.Series(0, index=df.index, dtype=int)
        sig[sq_prev & rel_now & (mom > 0)] = 1
        sig[sq_prev & rel_now & (mom < 0)] = -1
        return sig

class M04TripleEmaTrend(BaseBinaryStrategy):
    name = "M04_Triple_EMA_Trend"
    @classmethod
    def default_params(cls): return {"ema_fast": 9, "ema_mid": 21, "ema_slow": 55}
    @classmethod
    def optuna_space(cls, trial): return {"ema_fast": trial.suggest_int("ema_fast", 5, 13), "ema_mid": trial.suggest_int("ema_mid", 15, 30), "ema_slow": trial.suggest_int("ema_slow", 40, 80)}
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        p = self.params
        ef = df["close"].ewm(span=int(p["ema_fast"]), adjust=False).mean().shift(1)
        em = df["close"].ewm(span=int(p["ema_mid"]), adjust=False).mean().shift(1)
        es = df["close"].ewm(span=int(p["ema_slow"]), adjust=False).mean().shift(1)
        c = df["close"].shift(1)
        sig = pd.Series(0, index=df.index, dtype=int)
        sig[(ef > em) & (em > es) & (c > ef)] = 1
        sig[(ef < em) & (em < es) & (c < ef)] = -1
        return sig

class M05MacdStochMomentum(BaseBinaryStrategy):
    name = "M05_MACD_Stoch_Cross"
    @classmethod
    def default_params(cls): return {"stoch_low": 25.0, "stoch_high": 75.0}
    @classmethod
    def optuna_space(cls, trial): return {"stoch_low": trial.suggest_float("stoch_low", 15.0, 32.0), "stoch_high": trial.suggest_float("stoch_high", 68.0, 85.0)}
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        p = self.params
        macd = df["close"].ewm(span=12, adjust=False).mean() - df["close"].ewm(span=26, adjust=False).mean()
        hist = (macd - macd.ewm(span=9, adjust=False).mean()).shift(1)
        low14, high14 = df["low"].rolling(14).min(), df["high"].rolling(14).max()
        k = 100 * (df["close"] - low14) / (high14 - low14).replace(0, 1e-9)
        d = k.rolling(3).mean()
        sig = pd.Series(0, index=df.index, dtype=int)
        sig[(k.shift(2) <= d.shift(2)) & (k.shift(1) > d.shift(1)) & (k.shift(1) < p["stoch_low"]) & (hist > 0)] = 1
        sig[(k.shift(2) >= d.shift(2)) & (k.shift(1) < d.shift(1)) & (k.shift(1) > p["stoch_high"]) & (hist < 0)] = -1
        return sig

class M06SmartMoneyFVG(BaseBinaryStrategy):
    name = "M06_SMC_FVG_Imbalance"
    @classmethod
    def default_params(cls): return {"min_gap_pct": 0.0004}
    @classmethod
    def optuna_space(cls, trial): return {"min_gap_pct": trial.suggest_float("min_gap_pct", 0.0001, 0.0015)}
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        p = self.params
        c1 = df["close"].shift(1)
        sig = pd.Series(0, index=df.index, dtype=int)
        sig[((df["low"].shift(1) - df["high"].shift(3)) / c1) > p["min_gap_pct"]] = 1
        sig[((df["low"].shift(3) - df["high"].shift(1)) / c1) > p["min_gap_pct"]] = -1
        return sig

class M07VwapSigmaReversion(BaseBinaryStrategy):
    name = "M07_VWAP_Sigma_Reversion"
    @classmethod
    def default_params(cls): return {"window": 30, "sigma": 2.0}
    @classmethod
    def optuna_space(cls, trial): return {"window": trial.suggest_int("window", 15, 60), "sigma": trial.suggest_float("sigma", 1.5, 2.8)}
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        p = self.params
        w = int(p["window"])
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        vwap = ((tp * df["volume"]).rolling(w).sum() / df["volume"].rolling(w).sum()).shift(1)
        std = tp.rolling(w).std().shift(1)
        c = df["close"].shift(1)
        sig = pd.Series(0, index=df.index, dtype=int)
        sig[c < (vwap - p["sigma"] * std)] = 1
        sig[c > (vwap + p["sigma"] * std)] = -1
        return sig

class M08CciVolumeExhaustion(BaseBinaryStrategy):
    name = "M08_CCI_Volume_Exhaustion"
    @classmethod
    def default_params(cls): return {"cci_len": 20, "cci_thresh": 170.0, "vol_mult": 1.3}
    @classmethod
    def optuna_space(cls, trial): return {"cci_len": trial.suggest_int("cci_len", 14, 30), "cci_thresh": trial.suggest_float("cci_thresh", 130.0, 220.0), "vol_mult": trial.suggest_float("vol_mult", 1.1, 2.0)}
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        p = self.params
        n = int(p["cci_len"])
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        sma_tp = tp.rolling(n).mean()
        mad = tp.rolling(n).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True).replace(0, 1e-9)
        cci = ((tp - sma_tp) / (0.015 * mad)).shift(1)
        vol = df["volume"].shift(1)
        vol_sma = df["volume"].shift(2).rolling(20).mean()
        sig = pd.Series(0, index=df.index, dtype=int)
        sig[(cci < -p["cci_thresh"]) & (vol > vol_sma * p["vol_mult"])] = 1
        sig[(cci > p["cci_thresh"]) & (vol > vol_sma * p["vol_mult"])] = -1
        return sig

class M09FibGoldenPocket(BaseBinaryStrategy):
    name = "M09_Fib_GoldenPocket"
    @classmethod
    def default_params(cls): return {"swing_len": 34}
    @classmethod
    def optuna_space(cls, trial): return {"swing_len": trial.suggest_int("swing_len", 20, 60)}
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        n = int(self.params["swing_len"])
        hi, lo = df["high"].shift(2).rolling(n).max(), df["low"].shift(2).rolling(n).min()
        rng = (hi - lo).replace(0, np.nan)
        c1, c2 = df["close"].shift(1), df["close"].shift(2)
        sig = pd.Series(0, index=df.index, dtype=int)
        sig[(c1 >= hi - 0.786*rng) & (c1 <= hi - 0.618*rng) & (c1 > c2)] = 1
        sig[(c1 >= lo + 0.618*rng) & (c1 <= lo + 0.786*rng) & (c1 < c2)] = -1
        return sig

class M10EnsembleMetaLearner(BaseBinaryStrategy):
    name = "M10_Ensemble_MetaConsensus"
    @classmethod
    def default_params(cls): return {"min_votes": 2}
    @classmethod
    def optuna_space(cls, trial): return {"min_votes": trial.suggest_int("min_votes", 2, 4)}
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        bases = [M01RsiBollingerReversion, M02PriceActionPinBarSR, M03VolatilitySqueezeBreakout, M04TripleEmaTrend, M05MacdStochMomentum, M06SmartMoneyFVG, M07VwapSigmaReversion, M08CciVolumeExhaustion, M09FibGoldenPocket]
        votes = sum(c().generate_signals(df) for c in bases)
        mv = int(self.params["min_votes"])
        sig = pd.Series(0, index=df.index, dtype=int)
        sig[votes >= mv] = 1
        sig[votes <= -mv] = -1
        return sig

ALL_STRATEGIES: List[Type[BaseBinaryStrategy]] = [
    M01RsiBollingerReversion, M02PriceActionPinBarSR, M03VolatilitySqueezeBreakout,
    M04TripleEmaTrend, M05MacdStochMomentum, M06SmartMoneyFVG,
    M07VwapSigmaReversion, M08CciVolumeExhaustion, M09FibGoldenPocket, M10EnsembleMetaLearner,
]
