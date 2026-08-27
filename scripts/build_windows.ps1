[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "This build script only runs on Windows."
}

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$venvRoot = Join-Path $root ".venv"
$python = Join-Path (Join-Path $venvRoot "Scripts") "python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "The project virtual environment was not found."
}

$editableTarget = $root + "[release,build]"
& $python -m pip install --trusted-host mirrors.aliyun.com -e $editableTarget
if ($LASTEXITCODE -ne 0) {
    throw "Release build dependencies could not be installed."
}

& $python -m pytest
if ($LASTEXITCODE -ne 0) {
    throw "pytest failed before the release build."
}

& $python -m ruff check .
if ($LASTEXITCODE -ne 0) {
    throw "ruff failed before the release build."
}

$buildRoot = Join-Path $root "build"
$distRoot = Join-Path $root "dist"
foreach ($outputRoot in @($buildRoot, $distRoot)) {
    if (Test-Path -LiteralPath $outputRoot) {
        Remove-Item -LiteralPath $outputRoot -Recurse -Force
    }
}

$spec = Join-Path $root "customer_ledger.spec"
& $python -m PyInstaller --clean --noconfirm $spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed."
}

$candidateRoot = Join-Path $distRoot "CustomerLedger"
$exe = Join-Path $candidateRoot "CustomerLedger.exe"
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw "The build does not contain CustomerLedger.exe."
}

$internalRoot = Join-Path $candidateRoot "_internal"
$packageRoot = Join-Path $internalRoot "customer_ledger"
foreach ($requiredPath in @(
        (Join-Path $packageRoot "templates"),
        (Join-Path $packageRoot "static"),
        (Join-Path $internalRoot "migrations")
    )) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "The build is missing a required resource: $requiredPath"
    }
}

$forbidden = Get-ChildItem -LiteralPath $candidateRoot -Recurse -File |
    Where-Object {
        $_.Name -match '[.](db|sqlite|sqlite3|xls|xlsx|log)$' -or
        $_.FullName -match 'private_samples|runtime_data'
    }
if ($forbidden) {
    $names = $forbidden | ForEach-Object { $_.FullName }
    throw "The build contains forbidden runtime or sensitive files: $($names -join ', ')"
}

$sizeBytes = (Get-ChildItem -LiteralPath $candidateRoot -Recurse -File |
    Measure-Object -Property Length -Sum).Sum
$sizeMiB = [Math]::Round($sizeBytes / 1MB, 2)
Write-Host "Windows release candidate built: $candidateRoot"
Write-Host "EXE: $exe"
Write-Host "Directory size: $sizeMiB MiB"
