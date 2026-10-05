$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv venv --python 3.14 .venv
        uv pip install --python $projectPython -e '.[dev]'
    } else {
        py -3 -m venv .venv
        & $projectPython -m pip install -e '.[dev]'
    }
    if ($LASTEXITCODE -ne 0) { throw 'Environment installation failed' }
}
& $projectPython -m streamlit run frontend/app.py --server.address 127.0.0.1

