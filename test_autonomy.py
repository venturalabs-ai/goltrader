from core.config import settings
from main import run
import pandas as pd

# Test with auto_execute_dry_run=False (modo real)
df_board, result = run(symbol='BTCUSDT', interval='1', n_trials=12, auto_execute_dry_run=False)

print('\n=== RESULTADO ===')
champion_val = result.get("champion", {}).get("strategy", "N/A") if result.get("champion") else "N/A"
print(f'Champion: {champion_val}')
agent_decision = result.get("autonomous_decision", result.get("agent_decision", "N/A"))
print(f'Agent decision: {agent_decision}')

# Verificar se há execução real
receipt = result.get('execution_receipt')
if receipt:
    print(f'Receipt (execucao real): {receipt}')
else:
    print('Sem receipt direto (decisao HOLD ou filtros nao atendidos)')