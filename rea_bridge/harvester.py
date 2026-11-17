import ast
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Dict, Any

UPSTREAM_REPOS = {
    "freqtrade": "https://github.com/freqtrade/freqtrade.git",
    "backtrader": "https://github.com/mementum/backtrader.git",
    "vectorbt": "https://github.com/polakowo/vectorbt.git",
    "lean": "https://github.com/QuantConnect/Lean.git",
    "jesse": "https://github.com/jesse-ai/jesse.git",
    "hummingbot": "https://github.com/hummingbot/hummingbot.git",
    "pandas-ta": "https://github.com/twopirllc/pandas-ta.git",
    "optuna": "https://github.com/optuna/optuna.git",
    "mlflow": "https://github.com/mlflow/mlflow.git",
    "streamlit": "https://github.com/streamlit/streamlit.git",
}

class REAHybridHarvester:
    def __init__(self, base_dir: str = "upstream_repos", db_path: str = "memory/rea_dna.db"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS repo_capabilities (id INTEGER PRIMARY KEY AUTOINCREMENT, repo_name TEXT, file_path TEXT, symbol_type TEXT, symbol_name TEXT, docstring TEXT)")

    def clone_or_update_all(self) -> Dict[str, str]:
        status = {}
        for name, url in UPSTREAM_REPOS.items():
            target = self.base_dir / name
            if target.exists():
                cmd = ["git", "-C", str(target), "pull", "--ff-only"]
            else:
                cmd = ["git", "clone", "--depth", "1", url, str(target)]
            res = subprocess.run(cmd, capture_output=True, text=True)
            status[name] = "OK" if res.returncode == 0 else f"ERR: {res.stderr[:120]}"
        return status

    def index_python_ast_dna(self) -> int:
        records = []
        for repo_name in UPSTREAM_REPOS:
            repo_path = self.base_dir / repo_name
            if not repo_path.exists():
                continue
            for py_file in repo_path.rglob("*.py"):
                if any(p in py_file.parts for p in ("tests", "test", "__pycache__", "docs")):
                    continue
                try:
                    tree = ast.parse(py_file.read_text(encoding="utf-8", errors="ignore"))
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and not node.name.startswith("_"):
                            records.append((repo_name, str(py_file.relative_to(self.base_dir)), type(node).__name__, node.name, (ast.get_docstring(node) or "")[:200]))
                except Exception:
                    continue
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM repo_capabilities")
            conn.executemany("INSERT INTO repo_capabilities (repo_name, file_path, symbol_type, symbol_name, docstring) VALUES (?, ?, ?, ?, ?)", records)
        return len(records)

    def run_full_harvest(self) -> Dict[str, Any]:
        print("Clonando/atualizando os 10 repositórios upstream...")
        repos = self.clone_or_update_all()
        print("Indexando DNA arquitetural (classes e motores) no SQLite...")
        total = self.index_python_ast_dna()
        summary = {"repositories": repos, "indexed_capabilities": total, "dna_db": self.db_path}
        Path("memory/rea_dna_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        return summary

if __name__ == "__main__":
    print(json.dumps(REAHybridHarvester().run_full_harvest(), indent=2, ensure_ascii=False))
