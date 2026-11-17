<#
.SYNOPSIS
  Sobe o Goltrader no notebook em TESTNET: worker de execução + API (porta 8000) + frontend de desenvolvimento (porta 5173).
.DESCRIPTION
  FORÇA BYBIT_TESTNET=true, ANALYZER_MODE=true, GOLTRADER_ALLOW_LIVE=false e limpa a confirmação de conta real, mesmo que o .env
  diga outra coisa: o notebook NUNCA opera conta real (real só na VPS, com "CONFIRMAR" — veja docs/DEPLOY-VPS.md).
  Gera GOLTRADER_API_TOKEN no .env se estiver vazio. Logs em <raiz>\logs, PIDs em <raiz>\state\pids.json.
.EXAMPLE
  .\rodar-testnet.ps1            # sobe tudo
  .\rodar-testnet.ps1 -Status    # mostra a saúde do worker
  .\rodar-testnet.ps1 -Parar     # para tudo
#>
[CmdletBinding()]
param(
    [string]$Raiz = 'C:\Users\Ventura\goltrader',
    [switch]$SemFrontend,
    [switch]$Parar,
    [switch]$Status
)
. (Join-Path $PSScriptRoot '_comum.ps1')

if (-not (Test-Path -LiteralPath $Raiz -PathType Container)) { Stop-Erro "Pasta não encontrada: $Raiz (rode instalar-atualizar.ps1)" }
$Raiz = (Resolve-Path -LiteralPath $Raiz).Path
$estado = Join-Path $Raiz 'state'
$logs = Join-Path $Raiz 'logs'
New-Item -ItemType Directory -Force -Path $estado, $logs | Out-Null
$pidFile = Join-Path $estado 'pids.json'

function Stop-Servicos {
    if (-not (Test-Path -LiteralPath $pidFile)) { Write-Info 'Nenhum serviço registrado.'; return }
    $reg = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    foreach ($p in $reg.PSObject.Properties) {
        $proc = Get-Process -Id ([int]$p.Value) -ErrorAction SilentlyContinue
        if ($proc) { Write-Info "Parando $($p.Name) (PID $($p.Value))"; Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    }
    Remove-Item -LiteralPath $pidFile -Force
}

if ($Parar) { Stop-Servicos; return }

$py = Get-VenvPython $Raiz
if (-not $py) { Stop-Erro '.venv não encontrado. Rode .\scripts\windows\instalar-atualizar.ps1' }

if ($Status) {
    $env:GOLTRADER_STATE_DIR = $estado
    Push-Location $Raiz
    try { & $py -m execution.healthcheck 60; exit $LASTEXITCODE } finally { Pop-Location }
}

# --- .env: carrega, garante token, e FORÇA as travas de Testnet ---
$envPath = Join-Path $Raiz '.env'
$vars = Read-DotEnv $envPath
$token = if ($vars.ContainsKey('GOLTRADER_API_TOKEN')) { $vars['GOLTRADER_API_TOKEN'] } else { '' }
if ([string]::IsNullOrWhiteSpace($token)) {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create(); $rng.GetBytes($bytes); $rng.Dispose()
    $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    if (Test-Path -LiteralPath $envPath) {
        $linhas = @(Get-Content -LiteralPath $envPath -Encoding UTF8 | Where-Object { $_ -notmatch '^\s*GOLTRADER_API_TOKEN=' })
        Set-Content -LiteralPath $envPath -Value ($linhas + "GOLTRADER_API_TOKEN=$token") -Encoding UTF8
    } else { Set-Content -LiteralPath $envPath -Value "GOLTRADER_API_TOKEN=$token" -Encoding UTF8 }
    Write-Info 'GOLTRADER_API_TOKEN gerado e gravado no .env (informe-o na tela do painel).'
}
$vars['GOLTRADER_API_TOKEN'] = $token
foreach ($k in $vars.Keys) { Set-Item -Path ("Env:" + $k) -Value $vars[$k] }

if ($vars.ContainsKey('BYBIT_TESTNET') -and $vars['BYBIT_TESTNET'].ToLower() -ne 'true') { Write-Aviso 'O .env pede conta real, mas o notebook roda SOMENTE Testnet: forçando BYBIT_TESTNET=true.' }
$env:BYBIT_TESTNET = 'true'
$env:ANALYZER_MODE = 'true'
$env:GOLTRADER_ALLOW_LIVE = 'false'
$env:GOLTRADER_LIVE_CONFIRMATION = ''
$env:GOLTRADER_STATE_DIR = $estado
$env:GT_LOG_DIR = $logs
if (-not $vars.ContainsKey('GOLTRADER_DATA_SOURCE')) { $env:GOLTRADER_DATA_SOURCE = 'bybit' }

if (Test-Path -LiteralPath $pidFile) { Write-Info 'Havia serviços registrados; reiniciando.'; Stop-Servicos }
$reg = [ordered]@{}

function Start-Servico([string]$nome, [string]$exe, [string[]]$args_, [string]$cwd) {
    $out = Join-Path $logs "$nome.out.log"; $err = Join-Path $logs "$nome.err.log"
    $p = Start-Process -FilePath $exe -ArgumentList $args_ -WorkingDirectory $cwd -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
    Write-Info "$nome iniciado (PID $($p.Id)); logs: $out"
    return $p.Id
}

$reg['worker'] = Start-Servico 'worker' $py @('-m', 'execution.worker') $Raiz
$backend = Join-Path $Raiz 'backend'
if (Test-Path -LiteralPath (Join-Path $backend 'main.py')) {
    $reg['api'] = Start-Servico 'api' $py @('-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8000') $backend
} else { Write-Aviso 'backend\main.py não existe nesta versão: API não iniciada.' }
$front = Join-Path $Raiz 'frontend'
if (-not $SemFrontend -and (Test-Path -LiteralPath (Join-Path $front 'package.json')) -and (Test-Comando 'npm')) {
    $npm = (Get-Command npm).Source
    $reg['frontend'] = Start-Servico 'frontend' $npm @('run', 'dev', '--', '--host', '127.0.0.1') $front
}
($reg | ConvertTo-Json) | Set-Content -LiteralPath $pidFile -Encoding UTF8

Start-Sleep -Seconds 3
Write-Info 'Modo: TESTNET (forçado). Painel: http://127.0.0.1:5173  |  API: http://127.0.0.1:8000/api/saude'
Write-Info 'Status do worker: .\rodar-testnet.ps1 -Status   |   Parar: .\rodar-testnet.ps1 -Parar'
