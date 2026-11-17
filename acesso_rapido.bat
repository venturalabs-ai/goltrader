@echo off
title Acesso Rápido - Goltrader
cd C:\Users\Ventura\goltrader
echo.
echo ==========================================
echo   GOLTRADER - ACESSO RÁPIDO
echo ==========================================
echo.
echo [1] API Goltrader (Porta 8080) - Status e Docs
echo [2] Dashboard Streamlit (Porta 8501) - Tentar abrir
echo [3] Verificar saúde do sistema
echo [4] Sair
echo.
set /p choice="Escolha (1-3): "
if %choice%==1 goto api
if %choice%==2 goto dashboard
if %choice%==3 goto health
if %choice%==4 exit
goto menu

:api
echo.
echo Abrindo documentação da API...
start "" http://127.0.0.1:8080/docs
goto eof

:dashboard
echo.
echo Tentando iniciar Dashboard Streamlit...
cd C:\Users\Ventura\goltrader
.\.venv\Scripts\python.exe -m streamlit run dashboard.py --server.headless true --server.port 8501 --server.address 0.0.0.0 > nul 2>&1 &
timeout /t 5 /nobreak >nul
start "" http://127.0.0.1:8501
goto eof

:health
echo.
echo Status do sistema:
curl -s http://127.0.0.1:8080/health
timeout /t 3 /nobreak >nul
goto eof

:eof
echo.