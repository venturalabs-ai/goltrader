from core.config import settings
from main import run
import pandas as pd

# Modo REAL - auto_execute_dry_run=False (ordens reais para Bybit Mainnet)
df_board, result = run(symbol='BTCUSDT', interval='1', n_trials=12, auto_execute_dry_run=False)

print(f'\n=== TORNEIO GOLTRADER (EXECUCAO REAL) | BTCUSDT (1m) ===')
cols = ['trades', 'wins', 'win_rate', 'breakeven_wr', 'EV_por_trade', 'p_value', 'aprovado_bonferroni']
print(df_board[cols].round(4).to_string())

print('\nTelemetria Monte Carlo da Campeã:')
for mode, stats in result['monte_carlo'].items():
    print(f'  {mode:11s} -> {stats}')

print('\nDecisao Autonomo Goltrader:', result['agent_decision'])

# Verificar receipt (comprovante de execução)
exec_receipt = result.get('execution_receipt')
if exec_receipt:
    print('\nComprovante de Execucao:', exec_receipt)
else:
    print('\nSem comprovante de execucao direto (dry_run=False enviou ordens reais)')