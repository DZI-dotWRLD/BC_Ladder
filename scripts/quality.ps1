param(
    [switch]$SkipFullTests,
    [switch]$SkipAudit
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot "venv\Scripts\python.exe"
$PipAudit = Join-Path $RepoRoot "venv\Scripts\pip-audit.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing local virtual environment. Create it with: py -3.14 -m venv venv"
}

Push-Location $RepoRoot
try {
    & $Python manage.py check
    & $Python manage.py makemigrations --check --dry-run
    & $Python -m ruff format --check .
    & $Python -m ruff check .
    & git diff --check

    if (-not $SkipFullTests) {
        & $Python manage.py test --noinput
    }

    if (-not $SkipAudit) {
        if (Test-Path -LiteralPath $PipAudit) {
            & $PipAudit -r requirements.txt
        } else {
            & $Python -m pip_audit -r requirements.txt
        }
    }
}
finally {
    Pop-Location
}
