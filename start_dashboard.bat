@echo off
title Goltrader - Painel Completo (Testnet/Real)
cd /d C:\Users\Ventura\goltrader
echo.
echo ==========================================
echo   GOLTRADER - PAINEL COMPLETO
echo ==========================================
echo  Testnet e o padrao. Conta REAL so com toggle + CONFIRMAR no painel.
echo.
rem Para o painel antigo na porta 8501, se houver
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8501" ^| findstr "LISTENING"') do taskkill /PID %%p /F >nul 2>&1
echo Iniciando painel... Acesse: http://127.0.0.1:8501
start "" http://127.0.0.1:8501
.\.venv\Scripts\python.exe -m streamlit run dashboard.py --server.headless true --server.port 8501 --server.address 0.0.0.0
pause
