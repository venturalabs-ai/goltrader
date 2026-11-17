<#
.SYNOPSIS
  Instala ou atualiza o Goltrader híbrido no notebook (C:\Users\Ventura\goltrader) a partir do GitHub. SEMPRE faz backup antes.
.DESCRIPTION
  1) backup verificado (aborta se falhar)  2) git (clone/adoção/fast-forward do repositório público)
  3) venv + dependências  4) frontend (npm ci + build)  5) cria .env de Testnet se não existir (nunca sobrescreve).
  Se a pasta existe mas NÃO é um repositório git, a adoção sobrescreve arquivos rastreados com a versão do GitHub (o backup protege):
  isso exige -Confirmar. Nada aqui liga conta real: o notebook roda SOMENTE Testnet (ver rodar-testnet.ps1).
.EXAMPLE
  .\instalar-atualizar.ps1 -Confirmar
  .\instalar-atualizar.ps1 -Ref main -SemFrontend
#>
[CmdletBinding()]
param(
    [string]$Raiz = 'C:\Users\Ventura\goltrader',
    [string]$Repo = 'https://github.com/venturalabs-ai/goltrader.git',
    [string]$Ref = 'main',
    [string]$Destino = 'C:\Users\Ventura\goltrader_backups',
    [switch]$Confirmar,
    [switch]$SemFrontend,
    [switch]$SemDependencias,
    [switch]$SemBackup
)
. (Join-Path $PSScriptRoot '_comum.ps1')

if (-not (Test-Comando 'git')) { Stop-Erro 'git não encontrado. Instale: winget install Git.Git' }

$existe = Test-Path -LiteralPath $Raiz -PathType Container
$temGit = $existe -and (Test-Path -LiteralPath (Join-Path $Raiz '.git'))

# 1) Backup (só se já existe algo)
if ($existe -and -not $SemBackup) {
    $itens = @(Get-ChildItem -LiteralPath $Raiz -Force -ErrorAction SilentlyContinue)
    if ($itens.Count -gt 0) {
        Write-Info 'Fazendo backup antes de qualquer mudança...'
        & (Join-Path $PSScriptRoot 'backup-goltrader.ps1') -Raiz $Raiz -Destino $Destino | Out-Null
    }
}

# 2) Git
if (-not $existe) {
    Write-Info "Clonando $Repo em $Raiz"
    git clone --quiet $Repo $Raiz
    if ($LASTEXITCODE -ne 0) { Stop-Erro 'git clone falhou' }
    git -C $Raiz checkout --quiet $Ref
} elseif ($temGit) {
    $remoto = (git -C $Raiz remote get-url origin 2>$null)
    if ($remoto -and ($remoto.Trim().TrimEnd('/') -ne $Repo.TrimEnd('/')) -and ($remoto.Trim() -notmatch 'venturalabs-ai/goltrader(\.git)?$')) {
        Stop-Erro "O origin desta pasta é '$remoto', não $Repo. Nada foi alterado."
    }
    $sujo = git -C $Raiz status --porcelain --untracked-files=no
    if ($sujo) { Stop-Erro "Há alterações locais não commitadas em $Raiz (veja 'git status'). O backup foi feito; commit/stash e rode de novo." }
    Write-Info "Atualizando ($Ref)"
    git -C $Raiz fetch --quiet origin
    if ($LASTEXITCODE -ne 0) { Stop-Erro 'git fetch falhou' }
    git -C $Raiz checkout --quiet $Ref
    git -C $Raiz merge --ff-only --quiet "origin/$Ref"
    if ($LASTEXITCODE -ne 0) { Stop-Erro "Não deu fast-forward para origin/$Ref (histórico divergente). Nada foi sobrescrito." }
} else {
    if (-not $Confirmar) {
        Stop-Erro "A pasta $Raiz existe mas não é um repositório git. Adotar o repositório sobrescreve arquivos rastreados com a versão do GitHub (o backup acabou de ser feito). Rode de novo com -Confirmar."
    }
    Write-Info 'Adotando a pasta como repositório (backup já feito)'
    git -C $Raiz init --quiet
    git -C $Raiz remote add origin $Repo
    git -C $Raiz fetch --quiet origin
    if ($LASTEXITCODE -ne 0) { Stop-Erro 'git fetch falhou' }
    git -C $Raiz checkout -B $Ref "origin/$Ref" --force
    if ($LASTEXITCODE -ne 0) { Stop-Erro 'git checkout falhou' }
}
$sha = (git -C $Raiz rev-parse --short=12 HEAD)
Write-Info "Versão instalada: $sha"

# 3) Python / venv
if (-not $SemDependencias) {
    $py = if (Test-Comando 'py') { 'py' } elseif (Test-Comando 'python') { 'python' } else { $null }
    if (-not $py) { Stop-Erro 'Python 3.10+ não encontrado. Instale: winget install Python.Python.3.12' }
    $venv = Join-Path $Raiz '.venv'
    if (-not (Test-Path -LiteralPath $venv)) {
        Write-Info 'Criando ambiente virtual (.venv)'
        if ($py -eq 'py') { & py -3 -m venv $venv } else { & python -m venv $venv }
        if ($LASTEXITCODE -ne 0) { Stop-Erro 'falha ao criar o venv' }
    }
    $pip = Get-VenvPython $Raiz
    Write-Info 'Instalando dependências (worker + backend)'
    & $pip -m pip install --quiet --upgrade pip
    foreach ($req in @('deploy\requirements-worker.txt', 'backend\requirements.txt')) {
        $p = Join-Path $Raiz $req
        if (Test-Path -LiteralPath $p) { & $pip -m pip install --quiet -r $p; if ($LASTEXITCODE -ne 0) { Stop-Erro "pip falhou em $req" } }
        else { Write-Aviso "$req não existe nesta versão (PRs do Pulse/execução/deploy já foram para a main?)" }
    }
}

# 4) Frontend
$front = Join-Path $Raiz 'frontend'
if (-not $SemFrontend -and (Test-Path -LiteralPath (Join-Path $front 'package.json'))) {
    if (Test-Comando 'npm') {
        Write-Info 'Frontend: npm ci + build'
        Push-Location $front
        try { npm ci --no-audit --no-fund --silent; if ($LASTEXITCODE -ne 0) { Stop-Erro 'npm ci falhou' }; npm run build --silent; if ($LASTEXITCODE -ne 0) { Stop-Erro 'build do frontend falhou' } }
        finally { Pop-Location }
    } else { Write-Aviso 'npm não encontrado: pulei o frontend (winget install OpenJS.NodeJS.LTS).' }
}

# 5) .env de Testnet (nunca sobrescreve)
$envPath = Join-Path $Raiz '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    $modelo = Join-Path $PSScriptRoot 'notebook.env.example'
    if (Test-Path -LiteralPath $modelo) { Copy-Item -LiteralPath $modelo -Destination $envPath; Write-Info '.env de TESTNET criado a partir do modelo (preencha as chaves da Testnet nele).' }
} else { Write-Info '.env existente mantido (não foi alterado).' }

Write-Info 'Pronto. Próximo passo: .\scripts\windows\rodar-testnet.ps1'
