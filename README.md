# trading-agent

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

**Robinhood has no official trading API.** The included `RobinhoodBroker` uses
the unofficial `robin_stocks` library, which talks to reverse-engineered private
endpoints. Using it **violates Robinhood's Terms of Service** and can get your
account **restricted or permanently locked**. It is gated behind multiple
explicit opt-ins for that reason. If you want sanctioned automation, use a
broker with a real API — **Alpaca**, **Interactive Brokers**, or **Tradier** —
which you can add as a new adapter next to `robinhood.py`.

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
- **Broker** (`brokers/`): `PaperBroker` (default, simulated) or
  `RobinhoodBroker` (live, gated). One interface — swapping brokers changes
  nothing else.
- **Backtester** (`core/backtest.py`): event-driven, reuses the live risk/exec
  path, reports return, max drawdown, Sharpe, and a trade log.

## Risk configuration

All limits live in `config.example.yaml` under `risk:`. Defaults are
conservative (10% max position, 1% risk/trade, 5% stop, 3% daily-loss halt,
20% drawdown kill switch). Tune them deliberately — loosening them is how
accounts blow up.

## Going live (Robinhood) — the deliberately awkward path

You must do **all** of the following, or it stays in dry-run:

1. `pip install -e ".[robinhood]"`
2. Copy `.env.example` → `.env`, set `ROBINHOOD_USERNAME` / `ROBINHOOD_PASSWORD`.
3. In `config.yaml`: `broker: robinhood` **and** `allow_live: true`.
4. Run with the explicit flag: `trading-agent run --config config.yaml --i-understand-the-risks`

Schedule `run` on a cron (e.g. every 15 min during market hours) once you've
validated the strategy on paper. Prefer adding an Alpaca adapter instead.

## Tests

```bash
pytest -q
```

Covers the risk gate (position caps, halts, kill switch, stop sizing), the paper
broker (round trips, insufficient-cash rejection), and a full backtest run.
