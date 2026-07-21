# Container for running the trading loop 24/7 on an always-on host.
# Build:  docker build -t trading-agent .
# Run:    see DEPLOY.md (mounts /data for your config.yaml + OAuth token file)

FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir ".[yfinance,robinhood,config]"

# /data holds the two things you provide at runtime:
#   /data/config.yaml            -- your strategy + risk config
#   /data/robinhood_oauth.json   -- refreshable token file from `trading-agent login`
ENV ROBINHOOD_TOKEN_PATH=/data/robinhood_oauth.json
VOLUME ["/data"]

# Runs the unattended loop. Paper/dry-run unless config sets allow_live: true.
CMD ["trading-agent", "loop", "--config", "/data/config.yaml"]
