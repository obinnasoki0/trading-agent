# One-shot local setup for Windows PowerShell.
# From the repo folder, run:  .\setup.ps1
# If PowerShell refuses to run scripts, first run (once, answer Y):
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

$ErrorActionPreference = "Stop"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "python not found. Install Python 3.10+ from https://www.python.org/downloads/"
    Write-Host "IMPORTANT: tick 'Add python.exe to PATH' in the installer."
    exit 1
}

Write-Host "==> Creating virtualenv (.venv)"
python -m venv .venv
& .\.venv\Scripts\Activate.ps1

Write-Host "==> Installing trading-agent + extras (yfinance, robinhood MCP, yaml, tests)"
python -m pip install --quiet --upgrade pip
pip install --quiet -e ".[dev,yfinance,robinhood,config]"

Write-Host "==> Running the test suite"
pytest -q

Write-Host ""
Write-Host "All set. Next steps:"
Write-Host "  .\.venv\Scripts\Activate.ps1          # in any new PowerShell window"
Write-Host "  trading-agent strategies              # sanity check"
Write-Host "  copy config.robinhood.example.yaml config.yaml"
Write-Host "  trading-agent verify-robinhood        # after ROBINHOOD_MCP_TOKEN is set"
Write-Host "  trading-agent loop --config config.yaml    # DRY-RUN: no real orders"
Write-Host ""
Write-Host "Live trading stays off until you set allow_live: true in config.yaml"
Write-Host "AND pass --i-understand-the-risks. Paper-trade first."
