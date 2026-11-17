from core.config import settings
from main import run
import pandas as pd

# Modo REAL - auto_execute_dry_run=False
result = run(symbol='BTCUSDT', interval='1', n_trials=12, auto_execute_dry_run=False)
print(f'Status: {result.get("status")}')
if 'leaderboard' in result:
    df_board = pd.DataFrame(result['leaderboard']).set_index('strategy')
    cols = ['trades', 'wins', 'win_rate', 'breakeven_wr', 'EV_por_trade', 'p_value', 'aprovado_bonferroni']
    print(f'\n=== TORNEIO GOLTRADER (EXECUCAO REAL) | BTCUSDT (1m) ===')
    print(df_board[cols].round(4).to_string())
print('\nDecisao Autonomo Goltrader:', result.get('agent_decision', 'N/A'))