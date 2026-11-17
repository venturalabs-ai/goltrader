import json
from typing import Dict, Any
from openai import OpenAI
from core.config import settings
from core.hybrid_memory import HybridMemoryStore
from execution.bybit_hybrid_connector import BybitHybridConnector
from backtest.hybrid_engine import HybridBinaryBacktestEngine
from strategies.all_ten import ALL_STRATEGIES
from validation.stats import monte_carlo

class ContinuousSelfImprovingAgent:
    def __init__(self, model_name: str = None):
        self.model = model_name or settings.llm_model
        self.memory = HybridMemoryStore()
        self.connector = BybitHybridConnector()
        self.llm = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)

    def run_evolution_cycle(self, symbol: str = "BTCUSDT", interval: str = "1", payout: float = 0.85, n_trials_per_strategy: int = 12, auto_execute_dry_run: bool = True) -> Dict[str, Any]:
        df = self.connector.fetch_klines(symbol=symbol, interval=interval, limit=1000)
        engine = HybridBinaryBacktestEngine(payout=payout, expiry_candles=settings.expiry_candles, memory=self.memory)
        leaderboard, outcomes_map, strategies_map = [], {}, {}

        for strat_cls in ALL_STRATEGIES:
            metrics, oos_outcomes, trained_strat = engine.walk_forward_optimize_and_validate(df=df, strategy_cls=strat_cls, symbol=symbol, n_trials=n_trials_per_strategy, n_strategies_compared=len(ALL_STRATEGIES))
            leaderboard.append({"strategy": trained_strat.name, **metrics})
            outcomes_map[trained_strat.name] = oos_outcomes
            strategies_map[trained_strat.name] = trained_strat

        valid_board = sorted([r for r in leaderboard if r["trades"] > 0], key=lambda x: x["EV_por_trade"], reverse=True)
        if not valid_board:
            return {"status": "NO_TRADES_IN_OOS", "leaderboard": leaderboard}

        champ = valid_board[0]
        champ_name = champ["strategy"]
        self.memory.put_object(f"champion_{symbol}_{interval}", champ)
        self.memory.log_improvement_cycle(symbol, champ_name, champ["win_rate"], champ["EV_por_trade"], champ["p_value"], champ["best_params"])

        mc = {m: monte_carlo(outcomes_map[champ_name], payout=payout, mode=m) for m in ["fixed", "soros", "martingale"]}
        sig = int(strategies_map[champ_name].generate_signals(df).iloc[-1])
        decision = {"action": "EXECUTE_CALL" if (sig == 1 and champ["EV_por_trade"] > 0) else ("EXECUTE_PUT" if (sig == -1 and champ["EV_por_trade"] > 0) else "HOLD"), "bankroll_mode": "soros" if champ["win_rate"] >= 0.58 else "fixed", "recommended_stake_pct": 0.03}
        receipt = None
        if decision["action"] in ("EXECUTE_CALL", "EXECUTE_PUT"):
            direction = "CALL" if decision["action"] == "EXECUTE_CALL" else "PUT"
            receipt = self.connector.place_order(symbol=symbol, direction=direction, stake_usdt=30.0, expiry_candles=settings.expiry_candles, dry_run=auto_execute_dry_run)
        return {"champion": champ, "leaderboard": valid_board, "monte_carlo": mc, "latest_signal": sig, "agent_decision": decision, "execution_receipt": receipt}
