$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$previousPythonPath = $env:PYTHONPATH
Push-Location $root
try {
    uv sync --project person2_platform --python 3.12 --locked --all-extras --dev
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    uv sync --project person3_control_ui --python 3.12 --locked --dev
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $platformPython = Join-Path $root "person2_platform/.venv/Scripts/python.exe"
    $controlPython = Join-Path $root "person3_control_ui/.venv/Scripts/python.exe"

    $env:PYTHONPATH = "evals"
    & $platformPython -m pytest packages evals -q --basetemp=.pytest-person1
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $platformPython evals/run_evals.py --provider-mode mock --report .pytest-person1/eval_report.md
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $env:PYTHONPATH = "person2_platform/src"
    & $platformPython -m pytest person2_platform/tests -q --basetemp=.pytest-person2
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $env:PYTHONPATH = "person2_platform/src;person3_control_ui/src"
    & $controlPython -m pytest person3_control_ui/tests -q --basetemp=.pytest-person3
    exit $LASTEXITCODE
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
