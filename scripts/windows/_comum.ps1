# Funções comuns dos scripts do notebook (dot-source: . "$PSScriptRoot\_comum.ps1"). Compatível com Windows PowerShell 5.1 e PowerShell 7.
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$script:RaizPadrao   = 'C:\Users\Ventura\goltrader'
$script:BackupPadrao = 'C:\Users\Ventura\goltrader_backups'
$script:RepoUrl      = 'https://github.com/venturalabs-ai/goltrader.git'

function Write-Info([string]$msg) { Write-Host ("[{0}] {1}" -f (Get-Date -Format 'dd/MM/yyyy HH:mm:ss'), $msg) }
function Write-Aviso([string]$msg) { Write-Host ("[{0}] AVISO: {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg) -ForegroundColor Yellow }
function Stop-Erro([string]$msg) { Write-Host ("ERRO: {0}" -f $msg) -ForegroundColor Red; throw $msg }

function Test-Comando([string]$nome) { return [bool](Get-Command $nome -ErrorAction SilentlyContinue) }

# Lê um arquivo .env (CHAVE=valor) para um hashtable, sem executar nada. Ignora comentários e linhas vazias.
function Read-DotEnv([string]$caminho) {
    $r = @{}
    if (-not (Test-Path -LiteralPath $caminho)) { return $r }
    foreach ($linha in Get-Content -LiteralPath $caminho -Encoding UTF8) {
        $l = $linha.Trim()
        if ($l -eq '' -or $l.StartsWith('#')) { continue }
        $i = $l.IndexOf('=')
        if ($i -lt 1) { continue }
        $k = $l.Substring(0, $i).Trim()
        $v = $l.Substring($i + 1).Trim()
        if (($v.StartsWith('"') -and $v.EndsWith('"') -and $v.Length -ge 2) -or ($v.StartsWith("'") -and $v.EndsWith("'") -and $v.Length -ge 2)) {
            $v = $v.Substring(1, $v.Length - 2)
        }
        $r[$k] = $v
    }
    return $r
}

# Caminho do Python do venv do projeto (Windows: Scripts\python.exe; Linux/macOS: bin/python — usado só em testes).
function Get-VenvPython([string]$raiz) {
    $win = Join-Path $raiz '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $win) { return $win }
    $nix = Join-Path $raiz '.venv/bin/python'
    if (Test-Path -LiteralPath $nix) { return $nix }
    return $null
}

# Pastas que nunca entram no backup (dependências reconstruíveis / lixo).
$script:ExcluirNoBackup = @('.venv', 'venv', 'node_modules', '__pycache__', 'dist', 'upstream_repos', '.pytest_cache', '.mypy_cache')
