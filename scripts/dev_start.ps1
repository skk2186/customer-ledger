$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "未找到 .venv，请先执行：py -3.10 -m venv .venv，并安装 requirements-dev.txt。"
}

Push-Location $projectRoot
try {
    & $pythonPath -m flask --app "customer_ledger:create_app" db upgrade
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $pythonPath -m flask --app "customer_ledger:create_app" run --host 127.0.0.1 --port 5000
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
