"""Command-line entry point.

    trading-agent backtest --strategy sma_crossover --symbol AAPL --days 500
    trading-agent run      --config config.yaml            # one paper step
    trading-agent loop     --config config.yaml            # autonomous, unattended
    trading-agent strategies

Autonomy: `loop` runs unattended, deciding on a fixed cadence with NO human
approval step -- its only gate is the automated risk kill switch. Live Robinhood
trading additionally requires broker=robinhood, allow_live=true, and
--i-understand-the-risks. Anything less runs paper/dry-run.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from . import brokers
from .config import AgentConfig, load
from .core.backtest import Backtester, PortfolioBacktester
from .core.data import CSVData, SyntheticData, YFinanceData, make_window
from .core.engine import TradingEngine
from .core.risk import RiskManager
from .core.schedule import AutonomousRunner, Session
from . import strategies
from .signals.live import LiveNewsFeed
from .signals.news import NewsSignalSource, RSSNewsProvider, StubNewsProvider
from .strategies.blended import BlendedStrategy


def _data_provider(cfg: AgentConfig):
    if cfg.data_source == "alpaca":
        from .core.data import AlpacaData
        return AlpacaData(asset_class=cfg.asset_class)
    if cfg.data_source == "yfinance":
        return YFinanceData()
    if cfg.data_source == "csv":
        import os
        return CSVData(os.getenv("TRADING_DATA_DIR", "./data"))
    return SyntheticData()


def _news_source(cfg: AgentConfig, event_queue=None):
    """Return an object with .sentiment(symbol). Live feeds run in the background.

    If ``event_queue`` is given and the provider is live/streaming, each fresh
    headline pushes its symbol onto the queue so the loop can react immediately.
    """
    if cfg.news.provider in ("live", "alpaca"):
        on_news = None
        if event_queue is not None:
            on_news = lambda symbol, _hs: event_queue.put(symbol)  # noqa: E731
        feed = LiveNewsFeed(provider=RSSNewsProvider(), symbols=cfg.symbols,
                            poll_seconds=cfg.news.poll_seconds,
                            max_age_seconds=cfg.news.max_age_seconds, limit=cfg.news.limit,
                            on_news=on_news)
        if cfg.news.provider == "alpaca":
            from .signals.live import AlpacaNewsStream
            try:
                stream = AlpacaNewsStream(feed, cfg.symbols)
                threading_start(stream)  # push feed; falls back to polling if it fails
            except Exception as exc:
                print(f"Alpaca news stream unavailable ({exc}); falling back to polled RSS.")
        feed.poll_once()  # prime the cache immediately
        feed.start()
        return feed
    provider = RSSNewsProvider() if cfg.news.provider == "rss" else StubNewsProvider()
    return NewsSignalSource(provider=provider, limit=cfg.news.limit)


def threading_start(stream):
    import threading
    threading.Thread(target=stream.start, name="alpaca-news", daemon=True).start()


def _fundamentals_source(cfg: AgentConfig):
    from .signals.fundamentals import (
        FundamentalsSignalSource,
        StubFundamentals,
        YFinanceFundamentals,
    )
    provider = YFinanceFundamentals() if cfg.fundamentals.provider == "yfinance" else StubFundamentals()
    return FundamentalsSignalSource(provider=provider)


def _build_strategy(cfg: AgentConfig, override: str | None = None, news_source=None):
    base = strategies.build(override or cfg.strategy, **cfg.strategy_params)
    if not cfg.news.enabled and not cfg.fundamentals.enabled:
        return base
    news = (news_source or _news_source(cfg)) if cfg.news.enabled else None
    fundamentals = _fundamentals_source(cfg) if cfg.fundamentals.enabled else None
    w_news = cfg.news.weight if cfg.news.enabled else 0.0
    w_fund = cfg.fundamentals.weight if cfg.fundamentals.enabled else 0.0
    return BlendedStrategy(base, news=news, fundamentals=fundamentals,
                           w_tech=max(0.0, 1 - w_news - w_fund),
                           w_news=w_news, w_fund=w_fund)


def _build_broker(cfg: AgentConfig, understood: bool):
    return brokers.build(cfg.broker, cfg, understood)


def _mode(broker) -> str:
    return "LIVE" if broker.is_live and getattr(broker, "allow_live", False) else "PAPER/DRY-RUN"


def cmd_strategies(_args) -> int:
    for name, cls in strategies.REGISTRY.items():
        doc = cls.__doc__.splitlines()[0] if cls.__doc__ else ""
        print(f"  {name:16s} {doc}")
    print("  blended          Blend any of the above with news sentiment (news.enabled: true)")
    return 0


def cmd_backtest(args) -> int:
    cfg = load(args.config)
    strat = _build_strategy(cfg, args.strategy)
    risk = RiskManager(cfg.risk, tiers=cfg.risk_tiers)
    provider = _data_provider(cfg)
    start, end = make_window(args.days)

    if args.portfolio:
        data = {s: provider.history(s, start, end) for s in cfg.symbols}
        bt = PortfolioBacktester(strat, risk, cfg.starting_cash, cfg.commission, cfg.slippage_bps)
        result = bt.run(data)
        label = f"portfolio {cfg.symbols}"
    else:
        symbol = args.symbol or cfg.symbols[0]
        data = provider.history(symbol, start, end)
        bt = Backtester(strat, risk, cfg.starting_cash, cfg.commission, cfg.slippage_bps)
        result = bt.run(symbol, data)
        label = f"{symbol} ({len(data)} bars)"

    print(f"\nBacktest: {strat.name} on {label}")
    print(json.dumps(result.summary(), indent=2))
    if args.verbose:
        for t in result.trades:
            print(f"  {t['ts']}  {t['side']:4s} {t['qty']:.3f} @ {t['price']:.2f}  ({t['reason']})")
    return 0


def cmd_run(args) -> int:
    cfg = load(args.config)
    strat = _build_strategy(cfg)
    risk = RiskManager(cfg.risk, tiers=cfg.risk_tiers)
    broker = _build_broker(cfg, args.i_understand_the_risks)
    engine = TradingEngine(broker, strat, risk, _data_provider(cfg), cfg.symbols, cfg.lookback_days)

    actions = engine.step()
    acct = broker.account()
    print(f"[{_mode(broker)}] {strat.name} | equity=${acct.equity:,.2f} cash=${acct.cash:,.2f}")
    for a in actions or ["(no actions this step)"]:
        print(f"  {a}")
    return 0


def cmd_loop(args) -> int:
    cfg = load(args.config)
    risk = RiskManager(cfg.risk, tiers=cfg.risk_tiers)
    broker = _build_broker(cfg, args.i_understand_the_risks)

    # Event-driven mode: a fresh headline wakes the loop to trade that symbol now.
    event_queue = None
    news_source = None
    event_driven = args.event_driven and cfg.news.enabled and cfg.news.provider in ("live", "alpaca")
    if event_driven:
        import queue as _queue
        event_queue = _queue.Queue()
        news_source = _news_source(cfg, event_queue=event_queue)

    strat = _build_strategy(cfg, news_source=news_source)
    engine = TradingEngine(broker, strat, risk, _data_provider(cfg), cfg.symbols, cfg.lookback_days)

    interval = args.interval if args.interval is not None else cfg.interval_seconds
    session = Session(cfg.session)
    runner = AutonomousRunner(engine, interval_seconds=interval, session=session,
                              max_iterations=args.max_iterations, event_queue=event_queue)

    trigger = "news-event or " if event_driven else ""
    print(f"[{_mode(broker)}] autonomous loop: {strat.name} | session={session.value} "
          f"| {trigger}every {interval}s | risk={cfg.risk_profile}")
    print("  (unattended; automated risk kill switch is the only gate. Ctrl-C to stop.)")
    try:
        for ts, actions in runner.run():
            stamp = ts.strftime("%Y-%m-%d %H:%M:%S")
            acct = broker.account()
            print(f"[{stamp}] equity=${acct.equity:,.2f}")
            for a in actions:
                print(f"    {a}")
    except KeyboardInterrupt:
        print("\nStopped by user.")
    return 0


def cmd_login(args) -> int:
    """One-time interactive Robinhood OAuth. Opens a browser, saves refreshable
    tokens to a portable file you can move to an always-on server."""
    from .brokers.robinhood_mcp import RobinhoodMCPBroker

    broker = RobinhoodMCPBroker(token_path=args.token_path, interactive=True)
    try:
        tools = broker.login()
    except Exception as exc:
        print(f"Login failed: {exc}")
        return 1
    print(f"Success. The MCP exposes {len(tools)} tools.")
    print(f"Token file: {broker._storage.path}")
    print("Copy that file to your server (same path, or set ROBINHOOD_TOKEN_PATH) "
          "to run the loop unattended. Guard it -- it holds your refresh token.")
    return 0


def cmd_verify_robinhood(args) -> int:
    """Connect to the official Robinhood MCP, discover + auto-map its tools, and
    do a read-only account fetch to confirm everything lines up. Run this once
    after you have a ROBINHOOD_MCP_TOKEN before enabling live trading."""
    from .brokers.robinhood_mcp import RobinhoodMCPBroker

    broker = RobinhoodMCPBroker()
    try:
        broker.discover_and_map(verbose=True)
    except Exception as exc:
        print(f"\nCould not reach the Robinhood MCP: {exc}")
        print("Set ROBINHOOD_MCP_TOKEN (or connect via `claude mcp add robinhood-trading ...`).")
        return 1
    try:
        acct = broker.account()
        print(f"\nRead-only check OK: cash=${acct.cash:,.2f} equity=${acct.equity:,.2f} "
              f"positions={len(acct.positions)}")
    except Exception as exc:
        print(f"\nTool mapping needs adjustment (account fetch failed): {exc}")
        print("Edit brokers/robinhood_mcp.py:TOOL_MAP with the names printed above.")
        return 1
    print("\nRobinhood MCP verified. It stays dry-run until allow_live + --i-understand-the-risks.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trading-agent", description="Risk-first trading agent")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("backtest", help="Backtest a strategy on historical/synthetic data")
    b.add_argument("--strategy"); b.add_argument("--symbol")
    b.add_argument("--days", type=int, default=500)
    b.add_argument("--portfolio", action="store_true",
                   help="Backtest all configured symbols on one shared, risk-gated account.")
    b.add_argument("--config"); b.add_argument("--verbose", action="store_true")
    b.set_defaults(func=cmd_backtest)

    r = sub.add_parser("run", help="Run one live/paper decision step")
    r.add_argument("--config")
    r.add_argument("--i-understand-the-risks", action="store_true",
                   help="Required to place REAL Robinhood orders (ToS-violating).")
    r.set_defaults(func=cmd_run)

    lp = sub.add_parser("loop", help="Run the autonomous, unattended trading loop")
    lp.add_argument("--config")
    lp.add_argument("--interval", type=int, help="Seconds between cycles (overrides config)")
    lp.add_argument("--max-iterations", type=int, dest="max_iterations",
                    help="Stop after N cycles (for testing).")
    lp.add_argument("--event-driven", action="store_true", dest="event_driven",
                    help="React immediately to fresh news (needs news.provider live/alpaca).")
    lp.add_argument("--i-understand-the-risks", action="store_true",
                    help="Required to place REAL orders on a live broker.")
    lp.set_defaults(func=cmd_loop)

    s = sub.add_parser("strategies", help="List available strategies")
    s.set_defaults(func=cmd_strategies)

    lg = sub.add_parser("login", help="One-time Robinhood OAuth (durable, refreshable tokens)")
    lg.add_argument("--token-path", dest="token_path",
                    help="Where to save the token file (default ~/.trading-agent/robinhood_oauth.json)")
    lg.set_defaults(func=cmd_login)

    v = sub.add_parser("verify-robinhood",
                       help="Discover + auto-map the official Robinhood MCP tools")
    v.set_defaults(func=cmd_verify_robinhood)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
