# trading-agent

[![CI](https://github.com/obinnasoki0/trading-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/obinnasoki0/trading-agent/actions/workflows/ci.yml)

A modular, **risk-first** trading agent. Pluggable strategies and brokers, a
backtester that shares the exact risk/execution path with live trading, and a
hard risk manager you configure once and a strategy can never override.

It runs out of the box with **no credentials and no network** (synthetic data +
paper broker), so you can backtest and paper-trade immediately, then wire in
real data and a real broker when you're ready.

---

## ⚠️ Read this before you trade real money

**No trading agent can "maximize return" or guarantee profit.** Markets are
adversarial and largely unpredictable. What this project actually gives you is
*discipline*: a defined strategy executed consistently, with strict risk
controls that cap losses. That is the only durable edge software provides.

**Pick a sanctioned broker.** Two legal, supported paths ship here:
- **Alpaca** (`broker: alpaca`) — official REST API, free paper trading, and
  **24/7 crypto**. The simplest legal way to run this autonomously.
- **Robinhood Agentic Trading** (`broker: robinhood_mcp`) — Robinhood's
  **official MCP server** (`https://agent.robinhood.com/mcp/trading`, OAuth).
  Orders are sandboxed to a dedicated, separately-funded Agentic account; all
  other accounts stay read-only. Equities only at launch. See the section below.

The legacy `RobinhoodBroker` (`broker: robinhood`) uses the unofficial
`robin_stocks` library — reverse-engineered endpoints that **violate Robinhood's
ToS** and risk account lockout. It is kept only for reference and is
**discouraged now that the official MCP exists**. Don't use it.

**Always paper-trade and backtest first.** Prove a strategy over weeks of paper
trading before risking a cent. Past backtest performance does not predict future
results.

This software is provided as-is, with no warranty. You are solely responsible
for any financial losses and for compliance with your broker's terms and
applicable law.

---

## Install

```bash
git clone <your-repo-url> trading-agent && cd trading-agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                 # core + tests
pip install -e ".[dev,yfinance,config]" # + real data + YAML config
```

## Quick start (zero setup)

```bash
# List strategies
trading-agent strategies

# Backtest on synthetic data (no network needed, fully reproducible)
trading-agent backtest --strategy sma_crossover --symbol AAPL --days 750 --verbose

# Portfolio backtest: ALL configured symbols on one shared, risk-gated account
# (validates what the live loop actually does -- joint exposure/cash/drawdown caps)
trading-agent backtest --strategy momentum --portfolio --days 750

# One paper decision step
trading-agent run

# Autonomous, unattended loop (paper by default). Ctrl-C to stop.
trading-agent loop --config config.yaml
```

Use real historical data by setting `data_source: yfinance` in `config.yaml`
(and `pip install -e ".[yfinance]"`).

## Autonomy: trading unattended ("no human authorization")

`trading-agent loop` runs on a fixed cadence with **no human approval step** —
its only gate is the *automated* risk kill switch. That combination (unattended
+ real money) is the highest-risk way to run this, so the automated safety rails
are non-negotiable and tuned by your `risk_profile`.

- **Sessions** (`session:` in config) decide *when* it acts:
  - `equity` — US regular hours (09:30–16:00 ET, weekdays)
  - `extended` — pre/post market (≈04:00–20:00 ET, weekdays)
  - `always` — 24/7. **True round-the-clock trading only exists for crypto**;
    US stocks, including Robinhood, are not 24/7.
- **Cadence**: `interval_seconds` (default 900 = every 15 min).
- Run it as a long-lived process (systemd, a container, `nohup`, tmux). It
  auto-idles when the market is closed and resumes when it opens.

```bash
trading-agent loop --config config.yaml --interval 300   # decide every 5 min
```

## Reacting to current events (news + sentiment)

Set `news.enabled: true` to blend headline sentiment (geopolitics, banking,
industry, tech) with the technical signal:

```
final_strength = (1 - weight) * technical  +  weight * news_sentiment
```

- `provider: stub` — offline, deterministic (default; keeps tests/CI green).
- `provider: rss` — free Google-News RSS per symbol (needs network, no API key).
- Keep `weight` modest (≤0.3). **Honest caveat:** lexicon sentiment on headlines
  is a weak, noisy signal — it can't read nuance or "priced-in" news. Treat it as
  a small tilt, backtest any blend, and swap in a real NLP/news API
  (`signals/news.py` is the extension point) before leaning on it.

## Risk profiles

Pick a posture with `risk_profile: low | medium`, or specify the full `risk:`
block to override. Both keep you in low-to-medium-risk territory; `low` uses
smaller positions (5%), tighter stops, a 10% drawdown kill switch, and a 20%
cash floor.

## Architecture

```
CLI ──► TradingEngine / Backtester
             │
     ┌───────┼─────────────┐
  Strategy   RiskManager   Broker
 (signals)  (the gate)   (paper│robinhood│…)
             │
        DataProvider (synthetic│yfinance│csv)
```

- **Strategy** (`strategies/`): pure `price history -> Signal` in [-1, 1]. It never
  sizes positions or touches the broker. Ships with `sma_crossover`,
  `rsi_reversion`, `momentum`, and `blended` (technical + news). Add your own and
  register it in `strategies/__init__.py`.
- **Signals** (`signals/`): external inputs (news/sentiment) that tilt a strategy.
- **Schedule** (`core/schedule.py`): market sessions + the unattended
  `AutonomousRunner` loop.
- **RiskManager** (`core/risk.py`): the single gate. Position caps, stop-based
  sizing, daily-loss halt, and a drawdown kill switch. **This is the file that
  protects your capital.**
- **Broker** (`brokers/`): `PaperBroker` (default), `AlpacaBroker` (stocks +
  24/7 crypto), `RobinhoodMCPBroker` (official MCP), legacy `RobinhoodBroker`.
  One interface — swapping brokers changes nothing else.
- **Backtester** (`core/backtest.py`): event-driven, reuses the live risk/exec
  path, reports return, max drawdown, Sharpe, and a trade log.

## Risk configuration

All limits live in `config.example.yaml` under `risk:`. Defaults are
conservative (10% max position, 1% risk/trade, 5% stop, 3% daily-loss halt,
20% drawdown kill switch). Tune them deliberately — loosening them is how
accounts blow up.

## Going live — Alpaca (recommended)

1. `pip install -e ".[alpaca]"`
2. Copy `.env.example` → `.env`, set `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`
   (paper keys first!).
3. `config.yaml`: `broker: alpaca`. For **24/7 crypto** set `asset_class: crypto`,
   `session: always`, and use pairs like `BTC/USD` in `symbols`.
4. Paper by default. To go live: `allow_live: true` **and** run with
   `--i-understand-the-risks`.

```bash
trading-agent loop --config config.yaml                       # paper, safe
trading-agent loop --config config.yaml --i-understand-the-risks   # real money
```

## Going live — Robinhood Agentic Trading (official MCP)

Robinhood's sanctioned path. Two ways to use it:

**A) Agent-driven (Robinhood's intended flow, easiest auth).** Connect the MCP to
Claude Code and let the agent analyze + trade; OAuth is handled in the browser:

```bash
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
# then /mcp in Claude Code -> select robinhood-trading -> authenticate
```

**B) Loop-driven (this project's `RobinhoodMCPBroker`).** Our deterministic,
risk-managed engine drives the official MCP for unattended operation:

1. Fund your dedicated **Agentic account** in the Robinhood app (this caps your
   blast radius — only this account is tradable).
2. `pip install -e ".[robinhood]"` (installs the `mcp` SDK).
3. Provide an OAuth access token as `ROBINHOOD_MCP_TOKEN` (or use flow A).
4. `config.yaml`: `broker: robinhood_mcp`, `allow_live: true`; run with
   `--i-understand-the-risks`.
5. **Verify tool names first:** the MCP's exact tool schema is confirmed at
   runtime — `RobinhoodMCPBroker.list_tools()` prints them; map any differences
   in `brokers/robinhood_mcp.py:TOOL_MAP`. Until verified, it stays dry-run.

## Live news feed — trade on headlines as they publish

Set `news.provider: live` (polled RSS) or `alpaca` (near-real-time push via
Alpaca's news websocket). A background feed keeps fresh per-symbol sentiment that
the `blended` strategy reads every cycle, and new headlines can fire event-driven
decisions. Keep `weight` small and mind the latency caveat in `signals/live.py` —
by the time a retail headline is public it's often already priced in.

```yaml
news: { enabled: true, provider: live, weight: 0.25, poll_seconds: 30 }
```

**Event-driven mode** — react the instant a headline lands instead of waiting for
the next timer tick. Add `--event-driven`: each fresh headline wakes the loop to
evaluate just that symbol immediately (the risk caps still gate every order).

```bash
trading-agent loop --config config.yaml --event-driven
```

### Verifying the Robinhood MCP

Once you have a token (or connected via Claude Code), confirm the live tool
schema and auto-map it in one command — no code edits needed for name changes:

```bash
trading-agent verify-robinhood
```

It lists the server's real tools, auto-maps them to account/positions/quote/
order operations, and does a read-only balance check. It never places an order.

## Tests

```bash
pytest -q
```

Covers the risk gate (position caps, halts, kill switch, stop sizing), the paper
broker (round trips, insufficient-cash rejection), and a full backtest run.
