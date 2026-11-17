<#
.SYNOPSIS
  Backup (zip verificado) do Goltrader do notebook: C:\Users\Ventura\goltrader -> C:\Users\Ventura\goltrader_backups.
.DESCRIPTION
  Inclui código, .git, estado e logs. Exclui .venv, node_modules, __pycache__ etc. (reconstruíveis).
  O .env (segredos) entra no zip por padrão — o backup fica NA SUA MÁQUINA; guarde-o com cuidado e NUNCA o envie a ninguém.
  Use -ExcluirEnv para deixar o .env de fora. Mantém os últimos -Manter backups.
.EXAMPLE
  .\backup-goltrader.ps1
  .\backup-goltrader.ps1 -ExcluirEnv -Manter 5
#>
[CmdletBinding()]
param(
    [string]$Raiz = 'C:\Users\Ventura\goltrader',
    [string]$Destino = 'C:\Users\Ventura\goltrader_backups',
    [switch]$ExcluirEnv,
    [int]$Manter = 10
)
. (Join-Path $PSScriptRoot '_comum.ps1')

if (-not (Test-Path -LiteralPath $Raiz -PathType Container)) { Stop-Erro "Pasta não encontrada: $Raiz" }
$Raiz = (Resolve-Path -LiteralPath $Raiz).Path.TrimEnd('\', '/')
New-Item -ItemType Directory -Force -Path $Destino | Out-Null
$Destino = (Resolve-Path -LiteralPath $Destino).Path
if ($Destino.StartsWith($Raiz, [System.StringComparison]::OrdinalIgnoreCase)) { Stop-Erro "O destino do backup não pode ficar dentro da pasta do projeto ($Raiz)." }

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$carimbo = Get-Date -Format 'yyyyMMdd-HHmmss'
$zip = Join-Path $Destino "goltrader-$carimbo.zip"
$seq = 1
while (Test-Path -LiteralPath $zip) { $seq++; $zip = Join-Path $Destino "goltrader-$carimbo-$seq.zip" }
Write-Info "Backup de $Raiz -> $zip"

$excl = $script:ExcluirNoBackup
$arquivos = Get-ChildItem -LiteralPath $Raiz -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object {
    $rel = $_.FullName.Substring($Raiz.Length).TrimStart('\', '/')
    $partes = $rel -split '[\\/]'
    $dirs = if ($partes.Length -gt 1) { $partes[0..($partes.Length - 2)] } else { @() }
    $fora = $false
    foreach ($d in $dirs) { if ($excl -contains $d) { $fora = $true; break } }
    if (-not $fora -and $ExcluirEnv -and $_.Name -eq '.env' -and $partes.Length -ge 1) { $fora = $true }
    -not $fora
}

$n = 0
$fs = [System.IO.File]::Open($zip, [System.IO.FileMode]::CreateNew)
try {
    $arq = New-Object System.IO.Compression.ZipArchive($fs, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($f in $arquivos) {
            $rel = $f.FullName.Substring($Raiz.Length).TrimStart('\', '/').Replace('\', '/')
            try {
                [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($arq, $f.FullName, $rel, [System.IO.Compression.CompressionLevel]::Optimal)
                $n++
            } catch {
                Write-Aviso "Não consegui copiar (em uso?): $rel"
            }
        }
    } finally { $arq.Dispose() }
} finally { $fs.Dispose() }

# Verificação: abre o zip e confere o número de entradas.
$z = [System.IO.Compression.ZipFile]::OpenRead($zip)
try { $entradas = $z.Entries.Count } finally { $z.Dispose() }
if ($entradas -ne $n -or $n -eq 0) { Stop-Erro "Backup inconsistente (esperado $n, zip tem $entradas). Arquivo mantido para análise: $zip" }
$tam = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 2)
Write-Info "OK: $n arquivos, $tam MB, verificado. $(if ($ExcluirEnv) { '.env EXCLUÍDO.' } else { 'Contém o .env (segredos): guarde com cuidado.' })"

# Rotação
$antigos = Get-ChildItem -LiteralPath $Destino -Filter 'goltrader-*.zip' | Sort-Object LastWriteTime -Descending | Select-Object -Skip $Manter
foreach ($a in $antigos) { Remove-Item -LiteralPath $a.FullName -Force; Write-Info "Backup antigo removido: $($a.Name)" }
Write-Output $zip
