# Getting Started — step by step

This guide takes you from a funded Robinhood Agentic account to the
risk-managed loop running on **your own computer**. Total time: ~15 minutes.

> Everything below happens in the **Terminal application on your computer** —
> not in a Claude chat window. Mac: press `Cmd+Space`, type `Terminal`, press
> Enter. Windows: open **PowerShell** from the Start menu.

---

## Step 1 — Check your tools (1 min)

Type each line and press Enter:

```bash
git --version        # want: any version number
python3 --version    # want: 3.10 or higher
claude --version     # want: any version number
```

If one says "command not found":
- **git** → Mac: run `xcode-select --install` · Windows: https://git-scm.com/download/win
- **python3** → https://www.python.org/downloads/ (3.10+)
- **claude** → https://claude.com/claude-code (install Claude Code)

## Step 2 — Confirm your Robinhood connection (1 min)

```bash
claude mcp list
```

You should see `robinhood-trading` in the list. If you don't, run the connect
step again and complete the browser login:

```bash
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
claude        # start Claude, then type /mcp, pick robinhood-trading, authenticate
```

## Step 3 — Get the project (3 min)

```bash
git clone https://github.com/obinnasoki0/trading-agent
cd trading-agent
bash setup.sh
```

`setup.sh` installs everything and runs the test suite. Success looks like
**`34 passed`** near the end. If you see errors instead, copy them and ask
Claude for help.

## Step 4 — First backtest (1 min)

```bash
source .venv/bin/activate
trading-agent backtest --strategy momentum --portfolio --days 750
```

You'll get total return, max drawdown, Sharpe, and trade count on synthetic
data. This proves the engine works end to end on your machine.

## Step 5 — Map the live Robinhood tools (5 min)

The adapter needs to know the exact tool names Robinhood's server exposes.
Start Claude **inside the project folder**:

```bash
claude
```

Then ask it, in plain English:

> List the robinhood-trading MCP tools with their names and descriptions.
> Then update TOOL_MAP in src/trading_agent/brokers/robinhood_mcp.py to match,
> run pytest, and commit and push the change.

Claude has the Robinhood connection locally, so it can see the real tool list
and finish the mapping. (Alternatively, paste the tool list into your cloud
session and let that session update the repo.)

## Step 6 — Dry-run against your real account (read-only)

```bash
cp config.robinhood.example.yaml config.yaml
trading-agent verify-robinhood            # read-only: lists tools, checks balances
trading-agent loop --config config.yaml   # DRY-RUN: prints would-be trades, places NONE
```

Let the dry-run loop run during market hours for at least a few days. Watch
what it *would* do. If its decisions look wrong, tune the strategy/config and
backtest again — that's the whole point of this stage.

## Step 7 — Going live (only when YOU are ready)

Real orders require **all three**, deliberately:

1. In `config.yaml`, set `allow_live: true`
2. Your Agentic account is funded with money you can afford to lose
3. Run with the explicit flag:

```bash
trading-agent loop --config config.yaml --i-understand-the-risks
```

Keep it running in the background with `nohup` or `tmux`, or ask Claude to set
that up for you. The risk manager gates every order: position caps, stop-losses,
a daily-loss halt, and a drawdown kill switch that liquidates and stops.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `command not found: trading-agent` | Run `source .venv/bin/activate` first (every new terminal) |
| `No module named ...` | Re-run `bash setup.sh` |
| `verify-robinhood` says no token | Set `ROBINHOOD_MCP_TOKEN`, or do Step 5 via local Claude instead |
| Tests fail | Copy the error into Claude and ask it to fix |
| Loop idle outside 9:30–16:00 ET | Expected — equities session. See `session:` in config |

## Reality check (please read once)

No agent can guarantee profit. This one's job is discipline: a defined,
backtested strategy with hard risk caps. Fund the Agentic account only with
money you can afford to lose, dry-run before live, and expect drawdowns —
the kill switch exists because they happen.
