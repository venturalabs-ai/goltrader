import numpy as np
from scipy.stats import binomtest
from typing import Dict, Any

def breakeven_winrate(payout: float) -> float:
    return 1.0 / (1.0 + payout)

def evaluate_binary_outcomes(outcomes: np.ndarray, payout: float = 0.85, n_strategies_compared: int = 1, alpha: float = 0.05) -> Dict[str, Any]:
    n_trades = int(len(outcomes))
    p_be = breakeven_winrate(payout)
    alpha_bonf = alpha / max(1, n_strategies_compared)
    if n_trades == 0:
        return {"trades": 0, "wins": 0, "win_rate": 0.0, "breakeven_wr": round(p_be, 4), "EV_por_trade": -1.0, "p_value": 1.0, "alpha_bonferroni": round(alpha_bonf, 5), "aprovado_bonferroni": False}
    wins = int(np.sum(outcomes == 1))
    wr = wins / n_trades
    ev = wr * payout - (1.0 - wr)
    p_val = float(binomtest(k=wins, n=n_trades, p=p_be, alternative="greater").pvalue)
    return {"trades": n_trades, "wins": wins, "win_rate": round(wr, 4), "breakeven_wr": round(p_be, 4), "EV_por_trade": round(ev, 4), "p_value": round(p_val, 6), "alpha_bonferroni": round(alpha_bonf, 5), "aprovado_bonferroni": bool(p_val < alpha_bonf and ev > 0)}

def monte_carlo(outcomes: np.ndarray, payout: float = 0.85, mode: str = "fixed", initial_bankroll: float = 1000.0, base_pct: float = 0.02, n_sims: int = 3000, horizon_trades: int = 200, soros_levels: int = 3, seed: int = 42) -> Dict[str, float]:
    if len(outcomes) == 0:
        return {"ruin_prob": 1.0, "median_final": 0.0, "p95_max_dd": 1.0}
    rng = np.random.default_rng(seed)
    sampled = rng.random((n_sims, horizon_trades)) < float(np.mean(outcomes == 1))
    finals, dds, ruined = np.zeros(n_sims), np.zeros(n_sims), np.zeros(n_sims, dtype=bool)
    base_stake = initial_bankroll * base_pct
    mg_mult = (1.0 + payout) / payout
    for i in range(n_sims):
        bal, peak, max_dd, stake, s_step = initial_bankroll, initial_bankroll, 0.0, base_stake, 0
        for won in sampled[i]:
            if bal <= 0.01:
                ruined[i], bal = True, 0.0
                break
            stake = min(stake, bal)
            if won:
                prof = stake * payout
                bal += prof
                if mode == "soros":
                    s_step += 1
                    stake = (stake + prof) if s_step < soros_levels else base_stake
                    if s_step >= soros_levels: s_step = 0
                else:
                    stake = base_stake
            else:
                bal -= stake
                s_step = 0
                stake = (stake * mg_mult) if mode == "martingale" else base_stake
            peak = max(peak, bal)
            max_dd = max(max_dd, (peak - bal) / peak if peak > 0 else 1.0)
        finals[i], dds[i] = bal, max_dd
    return {"ruin_prob": round(float(np.mean(ruined)), 4), "median_final": round(float(np.median(finals)), 2), "p95_max_dd": round(float(np.percentile(dds, 95)), 4)}
