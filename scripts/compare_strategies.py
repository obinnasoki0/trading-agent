"""Compare strategies on the SAME historical data, head to head.

Run locally (needs network for real data):
    python scripts/compare_strategies.py --years 5
    python scripts/compare_strategies.py --data-source synthetic   # offline demo

Honest scope: news & fundamentals are NOT included -- we have no historical
headlines or point-in-time financials, and using today's values across old bars
would be look-ahead bias. So this compares the *price-based* logic: raw momentum
vs. the scorecard's trend + participation + reward/risk gating and conviction
sizing. It isolates whether the extra discipline helps on price alone.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from trading_agent import strategies
from trading_agent.core.backtest import PortfolioBacktester
from trading_agent.core.data import SyntheticData, YFinanceData
from trading_agent.core.risk import RiskLimits, RiskManager
from trading_agent.strategies.scorecard import ScorecardStrategy


def _build(name: str):
    if name == "scorecard":
        return ScorecardStrategy()          # price-only categories; news/fund neutral
    if name == "momentum":
        return strategies.build("momentum", lookback=20)
    return strategies.build(name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+",
                    default=["AAPL", "MSFT", "SPY", "NVDA", "AMD", "GOOGL"])
    ap.add_argument("--years", type=float, default=5)
    ap.add_argument("--data-source", choices=["yfinance", "synthetic"], default="yfinance")
    ap.add_argument("--risk", default="medium", help="low | medium | aggressive")
    ap.add_argument("--strategies", nargs="+", default=["momentum", "scorecard"])
    args = ap.parse_args()

    provider = SyntheticData() if args.data_source == "synthetic" else YFinanceData()
    end = datetime.now()
    start = end - timedelta(days=int(args.years * 365))

    data = {}
    for sym in args.symbols:
        try:
            data[sym] = provider.history(sym, start, end)
        except Exception as exc:
            print(f"  (skip {sym}: {exc})")
    if not data:
        print("No data fetched.")
        return 1

    print(f"\nData: {list(data)}  |  {args.years:g}y  |  source={args.data_source}  |  risk={args.risk}\n")
    print(f"{'strategy':14}{'return':>10}{'max drawdown':>15}{'sharpe':>9}{'trades':>8}")
    print("-" * 56)
    for name in args.strategies:
        rm = RiskManager(RiskLimits.from_profile(args.risk))
        bt = PortfolioBacktester(_build(name), rm, starting_cash=10_000)
        s = bt.run(data).summary()
        print(f"{name:14}{s['total_return']:>+9.1%}{s['max_drawdown']:>14.1%}"
              f"{s['sharpe']:>9.2f}{s['trades']:>8}")
    print("\nReminder: past backtest results do not predict future returns, and this\n"
          "excludes the news/fundamentals categories. Treat it as a sanity check.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
