"""Torneio Walk-Forward + Monte Carlo reaproveitando o motor do goltrader.

Replica agent.continuous_loop.run_evolution_cycle SEM enviar ordens (a execucao
fica no painel, sob os limites de risco). Usa as mesmas classes/funcoes do motor.
"""
from typing import Any, Callable, Dict, Optional

import numpy as np
import pandas as pd

from backtest.hybrid_engine import HybridBinaryBacktestEngine
from core.hybrid_memory import HybridMemoryStore
from strategies.all_ten import ALL_STRATEGIES
from validation.stats import monte_carlo, breakeven_winrate

STRATS = {c.name: c for c in ALL_STRATEGIES}

# Filtro de seguranca de main.py (mesmos limiares)
MAIN_SAFETY = {"min_win_rate": 0.55, "min_ev": 3.0, "min_trades": 15, "max_p_value": 0.05, "require_bonferroni": True}


def run_tournament(df: pd.DataFrame, symbol: str, interval: str, payout: float, expiry_candles: int,
                   n_trials: int, progress: Optional[Callable[[int, int, str], None]] = None,
                   memory: Optional[HybridMemoryStore] = None) -> Dict[str, Any]:
    memory = memory or HybridMemoryStore()
    engine = HybridBinaryBacktestEngine(payout=payout, expiry_candles=expiry_candles, memory=memory)
    board, outcomes = [], {}
    total = len(ALL_STRATEGIES)
    for i, cls in enumerate(ALL_STRATEGIES):
        if progress:
            progress(i, total, cls.name)
        metrics, oos, trained = engine.walk_forward_optimize_and_validate(
            df=df, strategy_cls=cls, symbol=symbol, n_trials=n_trials, n_strategies_compared=total)
        board.append({"strategy": trained.name, **metrics})
        outcomes[trained.name] = oos.astype(int).tolist()
    if progress:
        progress(total, total, "Monte Carlo")

    valid = sorted([r for r in board if r["trades"] > 0], key=lambda x: x["EV_por_trade"], reverse=True)
    res: Dict[str, Any] = {"symbol": symbol, "interval": interval, "payout": payout, "expiry_candles": expiry_candles,
                           "n_trials": n_trials, "candles": len(df), "data_ini": str(df.index[0]), "data_fim": str(df.index[-1]),
                           "ts": pd.Timestamp.now(tz="UTC").isoformat(), "leaderboard": board, "outcomes": outcomes}
    if not valid:
        res["status"] = "NO_TRADES_IN_OOS"
        res["champion"] = None
        return res

    champ = valid[0]
    name = champ["strategy"]
    memory.put_object(f"champion_{symbol}_{interval}", champ)
    memory.log_improvement_cycle(symbol, name, champ["win_rate"], champ["EV_por_trade"], champ["p_value"], champ["best_params"])
    oc = np.array(outcomes[name])
    res["monte_carlo"] = {m: monte_carlo(oc, payout=payout, mode=m) for m in ["fixed", "soros", "martingale"]}
    res["champion"] = champ
    res["status"] = "OK"
    res["main_safety_ok"] = bool(champ["win_rate"] >= MAIN_SAFETY["min_win_rate"] and champ["EV_por_trade"] >= MAIN_SAFETY["min_ev"]
                                 and champ["trades"] >= MAIN_SAFETY["min_trades"] and champ["p_value"] <= MAIN_SAFETY["max_p_value"]
                                 and champ["aprovado_bonferroni"])
    return res


def champion_signals(df: pd.DataFrame, champ: Dict[str, Any]) -> pd.Series:
    cls = STRATS[champ["strategy"]]
    return cls(params=champ.get("best_params") or {}).generate_signals(df)


def decide(sig: int, champ: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Mesma regra de decisao do agent/continuous_loop.py."""
    if not champ:
        return {"action": "HOLD", "bankroll_mode": "fixed", "recommended_stake_pct": 0.03, "signal": 0}
    ev = champ["EV_por_trade"]
    action = "EXECUTE_CALL" if (sig == 1 and ev > 0) else ("EXECUTE_PUT" if (sig == -1 and ev > 0) else "HOLD")
    return {"action": action, "bankroll_mode": "soros" if champ["win_rate"] >= 0.58 else "fixed",
            "recommended_stake_pct": 0.03, "signal": int(sig)}


def mc_fan(outcomes, payout: float, mode: str, initial: float = 1000.0, base_pct: float = 0.02, n_sims: int = 1000,
           horizon: int = 200, soros_levels: int = 3, seed: int = 42) -> Dict[str, Any]:
    """Caminhos de banca com a MESMA logica de validation.stats.monte_carlo, guardando a trajetoria."""
    oc = np.asarray(outcomes)
    if len(oc) == 0:
        return {}
    rng = np.random.default_rng(seed)
    sampled = rng.random((n_sims, horizon)) < float(np.mean(oc == 1))
    paths = np.zeros((n_sims, horizon + 1))
    base = initial * base_pct
    mg = (1.0 + payout) / payout
    for i in range(n_sims):
        bal, stake, step = initial, base, 0
        paths[i, 0] = bal
        for t, won in enumerate(sampled[i], start=1):
            if bal <= 0.01:
                paths[i, t:] = 0.0
                break
            stake = min(stake, bal)
            if won:
                prof = stake * payout
                bal += prof
                if mode == "soros":
                    step += 1
                    stake = (stake + prof) if step < soros_levels else base
                    if step >= soros_levels:
                        step = 0
                else:
                    stake = base
            else:
                bal -= stake
                step = 0
                stake = stake * mg if mode == "martingale" else base
            paths[i, t] = bal
    pct = {p: np.percentile(paths, p, axis=0) for p in (5, 25, 50, 75, 95)}
    return {"x": list(range(horizon + 1)), "pct": pct, "sample": paths[:30]}


def bankroll_stake(mode: str, base_stake: float, payout: float, recent_outcomes, soros_levels: int = 3) -> Dict[str, Any]:
    """Stake do proximo trade conforme gestao (fixed/soros/martingale) a partir do historico autonomo real."""
    stake, step = base_stake, 0
    mg = (1.0 + payout) / payout
    for won in recent_outcomes:  # do mais antigo ao mais novo
        if won == 1:
            if mode == "soros":
                step += 1
                stake = (stake + stake * payout) if step < soros_levels else base_stake
                if step >= soros_levels:
                    step = 0
            else:
                stake = base_stake
        elif won == 0:
            step = 0
            stake = stake * mg if mode == "martingale" else base_stake
    return {"stake": stake, "nivel": step}


def breakeven(payout: float) -> float:
    return breakeven_winrate(payout)
