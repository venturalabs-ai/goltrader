r"""Consulta saldo Bybit usando SOMENTE chaves do .env (nunca no codigo).

Uso:  .venv\Scripts\python.exe check_balance.py          -> testnet
      .venv\Scripts\python.exe check_balance.py real     -> conta real (somente leitura)
"""
import sys

from dashboard_components import keys
from dashboard_components.bybit_client import BybitClient

mode = keys.REAL if len(sys.argv) > 1 and sys.argv[1].lower() == "real" else keys.TESTNET
c = BybitClient(mode)
if not c.authenticated:
    print("chaves da conta real não configuradas" if mode == keys.REAL else "chaves da testnet não configuradas")
    sys.exit(1)
w = c.wallet()
print(f"[{mode.upper()}] conta {w['account_type']} | equity {w['total_equity']:.2f} USD | disponível {w['available']:.2f} USD")
for coin in w["coins"]:
    print(f"  {coin['moeda']}: saldo {coin['saldo']} (≈ {coin['usd']:.2f} USD)")
