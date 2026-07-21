#!/usr/bin/env bash
# One-shot local setup. Run from the repo root:  bash setup.sh
set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3.10+ first: https://www.python.org/downloads/"
  exit 1
fi

echo "==> Creating virtualenv (.venv)"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing trading-agent + extras (yfinance, robinhood MCP, yaml, tests)"
pip install --quiet --upgrade pip
pip install --quiet -e ".[dev,yfinance,robinhood,config]"

echo "==> Running the test suite"
pytest -q

echo
echo "All set. Next steps:"
echo "  source .venv/bin/activate            # in any new terminal"
echo "  trading-agent strategies             # sanity check"
echo "  cp config.robinhood.example.yaml config.yaml"
echo "  trading-agent verify-robinhood       # after ROBINHOOD_MCP_TOKEN is set"
echo "  trading-agent loop --config config.yaml   # DRY-RUN: no real orders"
echo
echo "Live trading stays off until you set allow_live: true in config.yaml"
echo "AND pass --i-understand-the-risks. Paper-trade first."
