"""Command-line entry point.

    trading-agent backtest --strategy sma_crossover --symbol AAPL --days 500
    trading-agent run      --config config.yaml            # one paper step
    trading-agent strategies

Live trading is deliberately awkward: you must set broker=robinhood AND
allow_live=true in config AND pass --i-understand-the-risks. Anything less
runs in paper/dry-run.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import AgentConfig, load
from .core.backtest import Backtester
from .core.data import SyntheticData, YFinanceData, CSVData, make_window
from .core.engine import TradingEngine
from .core.risk import RiskManager
from . import strategies


def _data_provider(cfg: AgentConfig):
    if cfg.data_source == "yfinance":
        return YFinanceData()
    if cfg.data_source == "csv":
        import os
        return CSVData(os.getenv("TRADING_DATA_DIR", "./data"))
    return SyntheticData()


def cmd_strategies(_args) -> int:
    for name, cls in strategies.REGISTRY.items():
        print(f"  {name:16s} {cls.__doc__.splitlines()[0] if cls.__doc__ else ''}")
    return 0


def cmd_backtest(args) -> int:
    cfg = load(args.config)
    strat = strategies.build(args.strategy or cfg.strategy, **cfg.strategy_params)
    risk = RiskManager(cfg.risk)
    provider = _data_provider(cfg)
    start, end = make_window(args.days)

    symbol = args.symbol or cfg.symbols[0]
    data = provider.history(symbol, start, end)
    bt = Backtester(strat, risk, cfg.starting_cash, cfg.commission, cfg.slippage_bps)
    result = bt.run(symbol, data)

    print(f"\nBacktest: {strat.name} on {symbol} ({len(data)} bars)")
    print(json.dumps(result.summary(), indent=2))
    if args.verbose:
        for t in result.trades:
            print(f"  {t['ts']}  {t['side']:4s} {t['qty']:.3f} @ {t['price']:.2f}  ({t['reason']})")
    return 0


def cmd_run(args) -> int:
    cfg = load(args.config)
    strat = strategies.build(cfg.strategy, **cfg.strategy_params)
    risk = RiskManager(cfg.risk)
    provider = _data_provider(cfg)

    if cfg.broker == "robinhood":
        from .brokers.robinhood import RobinhoodBroker
        live = cfg.allow_live and args.i_understand_the_risks
        if cfg.allow_live and not args.i_understand_the_risks:
            print("Refusing live trading without --i-understand-the-risks. Running dry-run.")
        broker = RobinhoodBroker(allow_live=live, dry_run=not live)
    else:
        from .brokers.paper import PaperBroker
        broker = PaperBroker(cfg.starting_cash, cfg.commission, cfg.slippage_bps)

    engine = TradingEngine(broker, strat, risk, provider, cfg.symbols, cfg.lookback_days)
    actions = engine.step()
    acct = broker.account()
    mode = "LIVE" if broker.is_live and getattr(broker, "allow_live", False) else "PAPER/DRY-RUN"
    print(f"[{mode}] {strat.name} | equity=${acct.equity:,.2f} cash=${acct.cash:,.2f}")
    for a in actions or ["(no actions this step)"]:
        print(f"  {a}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trading-agent", description="Risk-first trading agent")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("backtest", help="Backtest a strategy on historical/synthetic data")
    b.add_argument("--strategy"); b.add_argument("--symbol")
    b.add_argument("--days", type=int, default=500)
    b.add_argument("--config"); b.add_argument("--verbose", action="store_true")
    b.set_defaults(func=cmd_backtest)

    r = sub.add_parser("run", help="Run one live/paper decision step")
    r.add_argument("--config")
    r.add_argument("--i-understand-the-risks", action="store_true",
                   help="Required to place REAL Robinhood orders (ToS-violating).")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("strategies", help="List available strategies")
    s.set_defaults(func=cmd_strategies)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
