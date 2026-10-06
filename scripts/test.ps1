$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $env:PYTHONPATH = "packages;evals"
    python -m pytest packages evals -q --basetemp=.pytest-person1
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    python evals/run_evals.py --provider-mode mock
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $python = Join-Path $root "person3_control_ui/.venv/Scripts/python.exe"
    if (-not (Test-Path $python)) {
        uv sync --project person3_control_ui --python 3.12 --all-extras --dev
    }
    $env:PYTHONPATH = "person2_platform/src"
    & $python -m pytest person2_platform/tests -q --basetemp=.pytest-person2
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $env:PYTHONPATH = "person2_platform/src;person3_control_ui/src"
    & $python -m pytest person3_control_ui/tests -q --basetemp=.pytest-person3
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
