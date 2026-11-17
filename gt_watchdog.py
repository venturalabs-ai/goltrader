"""Watchdog Goltrader: reinicia autotrader (heartbeat velho/processo morto) e painel (porta 8501 fora)."""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
from dashboard_components import autostate as A  # noqa: E402

LOGF = ROOT / "logs" / "watchdog.log"
PYW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
FLAGS = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)


def log(msg):
    LOGF.parent.mkdir(exist_ok=True)
    if LOGF.exists() and LOGF.stat().st_size > 1_000_000:
        LOGF.replace(LOGF.with_suffix(".log.1"))
    with LOGF.open("a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def start(args):
    subprocess.Popen([str(PYW), *args], cwd=str(ROOT), creationflags=FLAGS, close_fds=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


def panel_ok():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8501/_stcore/health", timeout=8) as r:
            return r.read().decode().strip() == "ok"
    except Exception:
        return False


def port_pids(port):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
    return {ln.split()[-1] for ln in out.splitlines() if f":{port} " in ln and "LISTENING" in ln}


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else "all"
    if only in ("all", "autotrader") and not A.STOP_FLAG.exists():
        rt = A.load_runtime()
        age, pid = A.heartbeat_age(), rt.get("pid")
        alive = A.pid_alive(pid)
        if not alive or age > 180:
            if alive:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
                log(f"autotrader travado (heartbeat {age:.0f}s) — PID {pid} encerrado")
            start(["autotrader.py"])
            log("autotrader (re)iniciado")
    if only in ("all", "panel") and not panel_ok():
        for p in port_pids(8501):
            subprocess.run(["taskkill", "/PID", p, "/F"], capture_output=True)
        start(["launch_panel.py"])
        log("painel (re)iniciado")


if __name__ == "__main__":
    main()
