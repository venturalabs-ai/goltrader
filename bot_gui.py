#!/usr/bin/env python
"""Goltrader Bot Interface - GUI with Start Button

A professional-looking bot interface with a Start button that opens
the goltrader trading panel when clicked.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser
import subprocess
import sys
import os
import threading
import time


class GoltraderBot:
    def __init__(self, root):
        self.root = root
        self.root.title("Goltrader - Sistema Autônomo de Trading")
        self.root.geometry("400x500")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a2e")

        # Configuration
        self.api_url = "http://127.0.0.1:8080"
        self.dashboard_url = "http://127.0.0.1:8501"
        self.is_running = False

        # Style configuration
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.root.option_add("*Font", ("Segoe UI", 10))

        # Header Frame
        self.header_frame = tk.Frame(root, bg="#16213e", height=80)
        self.header_frame.pack(fill="x", padx=10, pady=10)
        self.header_frame.pack_propagate(False)

        # Title Label
        self.title_label = tk.Label(
            self.header_frame,
            text="🤖 GOLTRADER",
            bg="#16213e",
            fg="#e94560",
            font=("Segoe UI", 20, "bold")
        )
        self.title_label.pack(pady=(15, 0))

        # Subtitle Label
        self.subtitle_label = tk.Label(
            self.header_frame,
            text="Sistema Autônomo de Trading com IA",
            bg="#16213e",
            fg="#4cc9f0",
            font=("Segoe UI", 10)
        )
        self.subtitle_label.pack()

        # Main Content Frame
        self.main_frame = tk.Frame(root, bg="#111020", padx=20, pady=20)
        self.main_frame.pack(fill="both", expand=True)

        # Status Display
        self.status_frame = tk.LabelFrame(self.main_frame, text="Status do Sistema", bg="#111020", fg="#e0e0e0")
        self.status_frame.pack(fill="x", pady=(0, 15))

        self.status_label = tk.Label(
            self.status_frame,
            text="⏳ Sistema parado",
            bg="#111020",
            fg="#ffa500",
            font=("Segoe UI", 10)
        )
        self.status_label.pack(padx=10, pady=5)

        self.api_label = tk.Label(
            self.status_frame,
            text="API: Verificando...",
            bg="#111020",
            fg="#888888",
            font=("Segoe UI", 9)
        )
        self.api_label.pack(padx=10, pady=2)

        # Description
        self.desc_label = tk.Label(
            self.main_frame,
            text="Sistema de trading evolutivo com IA para Bybit Mainnet.\n\n"
                 "Funcionalidades:\n"
                 "• Análise evolutiva de 10 metodologias\n"
                 "• Filtros de segurança rigorosos\n"
                 "• Decisões autônomas HOLD/EXECUTE\n"
                 "• Telemetria Monte Carlo em tempo real",
            bg="#111020",
            fg="#cccccc",
            font=("Segoe UI", 9),
            justify="left"
        )
        self.desc_label.pack(anchor="w", pady=(10, 20))

        # Start Button - The main feature
        self.start_frame = tk.Frame(self.main_frame, bg="#111020")
        self.start_frame.pack(fill="x", pady=10)

        self.start_btn = tk.Button(
            self.start_frame,
            text="INICIAR SISTEMA ▶",
            command=self.start_system,
            bg="#e94560",
            fg="white",
            font=("Segoe UI", 14, "bold"),
            cursor="hand2",
            relief="flat",
            padx=20,
            pady=10
        )
        self.start_btn.pack(fill="x", padx=10, pady=5)

        # Configure button hover effect
        self.start_btn.bind("<Enter>", lambda e: self.start_btn.configure(bg="#ff6b6b"))
        self.start_btn.bind("<Leave>", lambda e: self.start_btn.configure(bg="#e94560"))

        # Bottom info frame
        self.bottom_frame = tk.Frame(self.main_frame, bg="#111020", height=80)
        self.bottom_frame.pack(fill="x", side="bottom", pady=10)
        self.bottom_frame.pack_propagate(False)

        self.info_label = tk.Label(
            self.bottom_frame,
            text="Porta API: 8080 | Dashboard: 8501 | Modo: Mainnet",
            bg="#111020",
            fg="#555555",
            font=("Segoe UI", 8)
        )
        self.info_label.pack(pady=5)

        self.version_label = tk.Label(
            self.bottom_frame,
            text="Goltrader v2.0 - Sistema Autônomo",
            bg="#111020",
            fg="#555555",
            font=("Segoe UI", 8)
        )
        self.version_label.pack()

        # Start the status checker thread
        self.check_status()

    def check_status(self):
        """Check the API status in a thread"""
        try:
            import urllib.request
            import json
            try:
                with urllib.request.urlopen(f"{self.api_url}/health", timeout=3) as resp:
                    data = json.loads(resp.read())
                    self.root.after(0, lambda: self.update_status(data))
            except Exception:
                self.root.after(0, lambda: self.status_label.configure(
                    text="⚠ API Indisponível", fg="#ff0000"))
        except:
            pass
        self.root.after(5000, self.check_status)

    def update_status(self, data):
        """Update the GUI status display"""
        if data.get("status") == "operational":
            self.status_label.configure(
                text="✅ Sistema Operacional",
                fg="#00e65c"
            )
            self.api_label.configure(
                text=f"API: {data.get('bybit_mode', 'N/A')} | {len(data.get('free_opencode_models', []))} modelos IA",
                fg="#00e65c"
            )
        else:
            self.status_label.configure(
                text="⚠ Sistema Parado",
                fg="#ffa500"
            )
            self.api_label.configure(
                text="API: Não conectada",
                fg="#ffa500"
            )

    def start_system(self):
        """Start the system - open the dashboard in browser"""
        if self.is_running:
            return

        self.is_running = True
        self.start_btn.configure(
            text="SISTEMA RODANDO ▶",
            bg="#00e65c",
            state="disabled"
        )
        self.status_label.configure(
            text="🤖 Sistema iniciando...",
            fg="#ffa500"
        )

        # Open the dashboard in default browser
        def open_browser():
            try:
                webbrowser.open(self.dashboard_url, new=2)
                self.root.after(0, lambda: self.status_label.configure(
                    text="📊 Painel aberto no navegador",
                    fg="#00e65c"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "Erro", f"Não foi possível abrir o navegador.\n\n"
                    f"Painel disponível em: {self.dashboard_url}"))

        # Run in separate thread to not block GUI
        threading.Thread(target=open_browser, daemon=True).start()

        # Also try to start the Streamlit if not running
        try:
            subprocess.Popen([
                sys.executable, "-m", "streamlit", "run", "dashboard.py",
                "--server.headless", "true",
                "--server.port", "8501",
                "--server.address", "0.0.0.0"
            ], cwd="C:\\Users\\Ventura\\goltrader",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except:
            pass

        # Check API health after 2 seconds
        self.root.after(2000, self.finalize_start)

    def finalize_start(self):
        """Finalize the start process"""
        try:
            import urllib.request
            import json
            try:
                with urllib.request.urlopen(f"{self.api_url}/health", timeout=3) as resp:
                    data = json.loads(resp.read())
                    if data.get("status") == "operational":
                        self.status_label.configure(
                            text="✅ Sistema Operacional - Mainnet Ativo",
                            fg="#00e65c")
                        self.api_label.configure(
                            text=f"API: Mainnet | {len(data.get('free_opencode_models', []))} modelos IA",
                            fg="#00e65c")
            except:
                pass
        except:
            pass
        self.start_btn.configure(state="normal")


def main():
    root = tk.Tk()
    app = GoltraderBot(root)
    root.mainloop()


if __name__ == "__main__":
    main()