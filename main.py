import pandas as pd
from core.config import settings
from agent.continuous_loop import ContinuousSelfImprovingAgent

def run(symbol: str = None, interval: str = None, n_trials: int = 12, auto_execute_dry_run: bool = False):
    """Run goltrader with full autonomy and safe entry filtering."""
    symbol = symbol or settings.default_symbol
    interval = interval or settings.default_interval

    from agent.continuous_loop import ContinuousSelfImprovingAgent
    agent = ContinuousSelfImprovingAgent()

    # Executar ciclo evolutivo completo
    result = agent.run_evolution_cycle(
        symbol=symbol,
        interval=interval,
        payout=settings.default_payout,
        n_trials_per_strategy=n_trials,
        auto_execute_dry_run=auto_execute_dry_run
    )

    # Aplicar filtro de entrada segura
    if result.get("leaderboard"):
        champion = result["leaderboard"][0]  # Melhor estratégia

        # Filtros de segurança rigorosos
        win_rate = champion.get("win_rate", 0)
        ev = champion.get("EV_por_trade", 0)
        trades = champion.get("trades", 0)
        p_value = champion.get("p_value", 1.0)
        approved = champion.get("aprovado_bonferroni", False)

        # Entrar apenas se atender todos os critérios de segurança
        safety_thresholds = {
            "min_win_rate": 0.55,
            "min_ev": 3.0,
            "min_trades": 15,
            "max_p_value": 0.05,
            "require_bonferroni": True
        }

        meets_criteria = (
            win_rate >= safety_thresholds["min_win_rate"] and
            ev >= safety_thresholds["min_ev"] and
            trades >= safety_thresholds["min_trades"] and
            p_value <= safety_thresholds["max_p_value"] and
            approved
        )

        if meets_criteria:
            # Entrar com operação segura
            direction = "CALL" if result.get("latest_signal", 0) == 1 else "PUT"
            stake_pct = min(0.03, 0.02 + (win_rate - 0.55) * 0.2)  # Ajuste baseado no win rate

            # Executar ordem real
            receipt = agent.connector.place_order(
                symbol=symbol,
                direction=direction,
                stake_usdt=30.0,
                expiry_candles=settings.expiry_candles,
                dry_run=False
            )

            result["autonomous_decision"] = {
                "action": f"EXECUTE_{direction}",
                "bankroll_mode": "soros" if win_rate >= 0.58 else "fixed",
                "recommended_stake_pct": round(stake_pct, 4),
                "confidence": "ALTA",
                "reason": f"Win rate {win_rate:.1%}, EV R${ev:.2f}, Trades {trades}, Aprovado bonferroni"
            }
        else:
            # Manter HOLD com explicação
            result["autonomous_decision"] = {
                "action": "HOLD",
                "bankroll_mode": "fixed",
                "recommended_stake_pct": round(0.02, 4),
                "confidence": "BAIXA",
                "reason": f"Filtro de segurança: WR {win_rate:.1%}, EV R${ev:.2f}, Trades {trades}, p-value {p_value:.4f}"
            }
    else:
        result["autonomous_decision"] = {
            "action": "HOLD",
            "bankroll_mode": "fixed",
            "recommended_stake_pct": 0.02,
            "confidence": "BAIXA",
            "reason": "Nenhuma estratégia válida encontrada na OOS window"
        }

    # Print resultados
    df_board = pd.DataFrame(result["leaderboard"]).set_index("strategy") if result.get("leaderboard") else pd.DataFrame()
    cols = ["trades", "wins", "win_rate", "breakeven_wr", "EV_por_trade", "p_value", "aprovado_bonferroni"]
    if not df_board.empty:
        print(f"\n=== TORNEIO GOLTRADER (10 METODOLOGIAS + REA DNA) | {symbol} ({interval}m) ===")
        print(df_board[cols].round(4).to_string())

    print("\nTelemetria Monte Carlo da Campeã:")
    if result.get("monte_carlo"):
        for mode, stats in result["monte_carlo"].items():
            print(f"  {mode:11s} -> {stats}")

    print("\nDecisão Autônoma Goltrader:", result.get("autonomous_decision", result.get("agent_decision", "N/A")))

    return df_board, result

if __name__ == "__main__":
    run()
