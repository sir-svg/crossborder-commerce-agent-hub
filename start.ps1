$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'Please install uv first: python -m pip install uv'
    exit 1
}
uv sync --frozen
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'Open http://127.0.0.1:8000 in your browser. Press Ctrl+C to stop.'
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
