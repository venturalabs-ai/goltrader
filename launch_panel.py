"""Sobe o painel Streamlit oculto em 0.0.0.0:8501 e fica esperando (usado pela tarefa agendada)."""
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
try:
    with urllib.request.urlopen("http://127.0.0.1:8501/_stcore/health", timeout=5) as r:
        if r.read().decode().strip() == "ok":
            sys.exit(0)  # ja esta rodando
except Exception:
    pass
py = ROOT / ".venv" / "Scripts" / "python.exe"
cmd = [str(py), "-m", "streamlit", "run", "dashboard.py", "--server.headless", "true",
       "--server.port", "8501", "--server.address", "0.0.0.0"]
logf = open(ROOT / "logs" / "panel.log", "a", encoding="utf-8")
rc = subprocess.call(cmd, cwd=str(ROOT), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), stdout=logf, stderr=logf)
sys.exit(rc)
