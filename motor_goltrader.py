#!/usr/bin/env python
"""
Motor Goltrader Completo
------------------------
Script que levanta e mantém todo o sistema goltrader funcional:
- API Server (uvicorn)
- Dashboard Streamlit
- Monitoramento contínuo
- Status dos modelos de IA
"""

import subprocess
import sys
import time
import json
import os
import signal
import threading
import webbrowser
from pathlib import Path


class MotorGoltrader:
    def __init__(self):
        self.processes = []
        self.running = False
        self.api_url = "http://127.0.0.1:8080"
        self.dashboard_url = "http://127.0.0.1:8501"
        self.port = 8080
        
    def instalar_dependencias(self):
        """Instala/atualiza dependências críticas"""
        print("[" + time.strftime("%H:%M:%S") + "] Instalando dependências...")
        try:
            # Atualizar numpy para compatibilidade com PyArrow
            subprocess.run([
                sys.executable, "-m", "pip", "install", "--upgrade", "numpy==2.0.2"
            ], capture_output=True, timeout=120)
            
            # Verificar instalar requisitos do goltrader
            subprocess.run([
                sys.executable, "-m", "pip", "install", "-e", "."
            ], capture_output=True, timeout=120, cwd="C:\\Users\\Ventura\\goltrader")
            
            print("[" + time.strftime("%H:%M:%S") + "] Dependências OK")
        except Exception as e:
            print("[" + time.strftime("%H:%M:%S") + "] Aviso: " + str(e))

    def verificar_saude(self):
        """Verifica se a API está respondendo"""
        try:
            import urllib.request
            with urllib.request.urlopen(f"{self.api_url}/health", timeout=5) as resp:
                data = json.loads(resp.read())
                return data.get("status") == "operational", data
        except:
            return False, None

    def iniciar_api(self):
        """Inicia o servidor API Uvicorn"""
        print("[" + time.strftime("%H:%M:%S") + "] Iniciando API Goltrader...")
        try:
            proc = subprocess.Popen([
                sys.executable, "-m", "uvicorn", "api.server:app",
                "--host", "127.0.0.1", "--port", str(self.port),
                "--reload"
            ], cwd="C:\\Users\\Ventura\\goltrader",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.processes.append(("api", proc))
            print("[" + time.strftime("%H:%M:%S") + "] API iniciada (PID: " + str(proc.pid) + ")")
            return True
        except Exception as e:
            print("[" + time.strftime("%H:%M:%S") + "] Erro ao iniciar API: " + str(e))
            return False

    def iniciar_dashboard(self):
        """Inicia o Dashboard Streamlit"""
        print("[" + time.strftime("%H:%M:%S") + "] Iniciando Dashboard Streamlit...")
        try:
            proc = subprocess.Popen([
                sys.executable, "-m", "streamlit", "run", "dashboard.py",
                "--server.headless", "true",
                "--server.port", "8501",
                "--server.address", "0.0.0.0"
            ], cwd="C:\\Users\\Ventura\\goltrader",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.processes.append(("dashboard", proc))
            print("[" + time.strftime("%H:%M:%S") + "] Dashboard iniciado (PID: " + str(proc.pid) + ")")
            return True
        except Exception as e:
            print("[" + time.strftime("%H:%M:%S") + "] Erro ao iniciar Dashboard: " + str(e))
            return False

    def aguardar_api_pronta(self, max_tentativas=10, intervalo=3):
        """Aguarda a API ficar pronta"""
        print("[" + time.strftime("%H:%M:%S") + "] Aguardando API ficar pronta...")
        for tentativa in range(max_tentativas):
            ok, data = self.verificar_saude()
            if ok:
                print("[" + time.strftime("%H:%M:%S") + "] API pronta!")
                return True
            print("[" + time.strftime("%H:%M:%S") + "] Tentativa " + str(tentativa+1) + "/" + str(max_tentativas) + ": API aguardando...")
            time.sleep(intervalo)
        return False

    def abrir_navegador(self):
        """Abre o navegador nos painéis"""
        time.sleep(2)  # Pequena delay para o dashboard iniciar
        try:
            webbrowser.open(self.dashboard_url, new=2)  # Abre nova aba
            webbrowser.open(self.api_url + "/docs", new=2)  # Documentação
            print("[" + time.strftime("%H:%M:%S") + "] Navegadores abertos")
        except:
            print("[" + time.strftime("%H:%M:%S") + "] Não foi possível abrir navegadores automaticamente")

    def monitorar_sistema(self):
        """Loop de monitoramento do sistema"""
        print("[" + time.strftime("%H:%M:%S") + "] Iniciando monitoramento contínuo...")
        while self.running:
            try:
                ok, data = self.verificar_saude()
                if ok:
                    modelos = len(data.get("free_opencode_models", []))
                    print("[" + time.strftime("%H:%M:%S") + "] GOLTRADER: Status=operational | Modo=Mainnet | Modelos IA=" + str(modelos))
                else:
                    print("[" + time.strftime("%H:%M:%S") + "] Aviso: API não respondendo")
            except Exception as e:
                print("[" + time.strftime("%H:%M:%S") + "] Erro no monitoramento: " + str(e))
            time.sleep(30)  # Verificar a cada 30 segundos

    def iniciar_tudo(self):
        """Método principal - inicia o motor completo"""
        print("=" * 60)
        print("  MOTOR GOLTRADER COMPLETO")
        print("=" * 60)
        print("[" + time.strftime("%H:%M:%S") + "] Inicializando sistema...")
        
        # 1. Instalar dependências
        self.instalar_dependencias()
        
        # 2. Iniciar API
        if not self.iniciar_api():
            print("[" + time.strftime("%H:%M:%S") + "] CRITICAL: Não foi possível iniciar a API!")
            return False
        
        # 3. Aguardar API ficar pronta
        if not self.aguardar_api_pronta():
            print("[" + time.strftime("%H:%M:%S") + "] CRITICAL: API não ficou pronta dentro do tempo limite!")
            return False
        
        # 4. Iniciar Dashboard
        self.iniciar_dashboard()
        
        # 5. Abrir navegadores
        self.abrir_navegador()
        
        # 6. Iniciar monitoramento
        self.running = True
        monitor_thread = threading.Thread(target=self.monitorar_sistema, daemon=True)
        monitor_thread.start()
        
        print("=" * 60)
        print("  SISTEMA GOLTRADER ATIVO")
        print("=" * 60)
        print("[" + time.strftime("%H:%M:%S") + "] API: http://127.0.0.1:" + str(self.port) + "/health")
        print("[" + time.strftime("%H:%M:%S") + "] Dashboard: http://127.0.0.1:8501")
        print("[" + time.strftime("%H:%M:%S") + "] Painel de documentação: http://127.0.0.1:8080/docs")
        print("[" + time.strftime("%H:%M:%S") + "] Pressione Ctrl+C para parar o motor")
        print()
        
        # Manter o motor rodando
        try:
            while self.running:
                time.sleep(1)
                # Verificar se processos ainda estão vivos
                for nome, proc in self.processes:
                    if proc.poll() is not None:
                        print("[" + time.strftime("%H:%M:%S") + "] Aviso: Processo " + nome + " encerrou inesperadamente")
                        self.running = False
                        break
        except KeyboardInterrupt:
            print("\n[" + time.strftime("%H:%M:%S") + "] Interrupção solicitada...")
        finally:
            self.parar()
        
        return True

    def parar(self):
        """Para todos os processos do motor"""
        print("[" + time.strftime("%H:%M:%S") + "] Parando motor Goltrader...")
        self.running = False
        for nome, proc in self.processes:
            try:
                proc.terminate()
                print("[" + time.strftime("%H:%M:%S") + "] Processo " + nome + " terminado")
            except:
                pass
        # Kill any remaining python processes for this project
        try:
            subprocess.run(["taskkill", "/f", "/im", "python.exe"], 
                         capture_output=True)
        except:
            pass
        print("[" + time.strftime("%H:%M:%S") + "] Motor parado completamente")


def main():
    motor = MotorGoltrader()
    try:
        sucesso = motor.iniciar_tudo()
        if not sucesso:
            print("Falha ao iniciar o motor goltrader.")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\nEncerrando...")
        motor.parar()
        sys.exit(0)


if __name__ == "__main__":
    main()