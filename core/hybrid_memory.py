import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

class HybridMemoryStore:
    def __init__(self, db_path: str = "memory/goltrader_memory.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.optuna_storage_url = f"sqlite:///{Path(db_path).parent / 'optuna_studies.db'}"
        self.mlflow_tracking_uri = f"sqlite:///{Path(db_path).parent / 'mlflow_tracking.db'}"
        self._init_schema()

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS object_store (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL)")
            conn.execute("""CREATE TABLE IF NOT EXISTS trade_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp_utc TEXT NOT NULL, symbol TEXT NOT NULL,
                interval TEXT NOT NULL, strategy_name TEXT NOT NULL, direction TEXT NOT NULL,
                stake_usdt REAL NOT NULL, payout REAL NOT NULL, mode TEXT NOT NULL,
                outcome INTEGER, pnl_usdt REAL, metadata_json TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS improvement_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp_utc TEXT NOT NULL, symbol TEXT NOT NULL,
                champion_strategy TEXT NOT NULL, oos_win_rate REAL NOT NULL, oos_ev REAL NOT NULL,
                p_value REAL NOT NULL, best_params_json TEXT NOT NULL)""")

    def put_object(self, key: str, value: Any):
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO object_store (key, value_json, updated_at) VALUES (?, ?, ?)", (key, json.dumps(value, ensure_ascii=False), now))

    def log_trade(self, symbol: str, interval: str, strategy_name: str, direction: str, stake_usdt: float, payout: float, mode: str, outcome: Optional[int] = None, metadata: Optional[Dict[str, Any]] = None):
        pnl = (stake_usdt * payout if outcome == 1 else -stake_usdt) if outcome is not None else None
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO trade_ledger (timestamp_utc, symbol, interval, strategy_name, direction, stake_usdt, payout, mode, outcome, pnl_usdt, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (now, symbol, interval, strategy_name, direction, stake_usdt, payout, mode, outcome, pnl, json.dumps(metadata or {}, ensure_ascii=False)))

    def log_improvement_cycle(self, symbol: str, champion_strategy: str, oos_win_rate: float, oos_ev: float, p_value: float, best_params: Dict[str, Any]):
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO improvement_cycles (timestamp_utc, symbol, champion_strategy, oos_win_rate, oos_ev, p_value, best_params_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (now, symbol, champion_strategy, oos_win_rate, oos_ev, p_value, json.dumps(best_params)))

    def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM trade_ledger ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
