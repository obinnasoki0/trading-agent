"""Tradable universes -- the candidate pool the scorecard ranks over.

Instead of a hand-typed `symbols` list, point the agent at a broad universe; it
scores every name and trades only the top-ranked ones that qualify (bounded by
`max_positions` and your risk caps). With a small account you still *hold* only a
few names -- the universe just widens the pool you pick the best from.

Honest limit: free data (yfinance/RSS) rate-limits on big scans, and each symbol
is fetched sequentially, so hundreds of names per cycle is slow. Keep universes
in the tens-to-low-hundreds, or move to a batch/paid feed for "everything."
"""

from __future__ import annotations

# ~70 liquid US large caps across sectors. Not the full S&P 500 (that scans slow
# on free data) -- a practical, diversified default. Supply your own list for more.
LARGECAP = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "AVGO", "TSLA", "AMD", "NFLX",
    "ADBE", "CRM", "ORCL", "CSCO", "INTC", "QCOM", "TXN", "IBM", "NOW", "INTU",
    "JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "AXP", "BLK", "SPGI",
    "V", "MA", "PYPL", "BRK-B", "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV",
    "TMO", "ABT", "DHR", "AMGN", "PG", "KO", "PEP", "COST", "WMT", "MCD",
    "HD", "NKE", "SBUX", "DIS", "CMCSA", "T", "VZ", "XOM", "CVX", "COP",
    "CAT", "BA", "HON", "GE", "UPS", "LMT", "RTX", "LIN", "SPY", "QQQ",
]

# Alpaca-tradable crypto pairs (slash format). Alpaca lists dozens; these are the
# most liquid. 24/7.
CRYPTO = [
    "BTC/USD", "ETH/USD", "SOL/USD", "LTC/USD", "BCH/USD", "LINK/USD",
    "UNI/USD", "AAVE/USD", "AVAX/USD", "DOT/USD", "DOGE/USD", "SHIB/USD",
    "XRP/USD", "MATIC/USD", "ADA/USD",
]

UNIVERSES: dict[str, list[str]] = {
    "largecap": LARGECAP,
    "sp500": LARGECAP,       # alias; swap in the full list if you bring one
    "crypto": CRYPTO,
}


def resolve(spec) -> list[str]:
    """Turn a universe spec into a symbol list.

    Accepts: a list (used as-is), a preset name ("largecap"/"crypto"/...), or a
    comma-separated string.
    """
    if isinstance(spec, (list, tuple)):
        return list(spec)
    if isinstance(spec, str):
        key = spec.strip().lower()
        if key in UNIVERSES:
            return list(UNIVERSES[key])
        return [s.strip() for s in spec.split(",") if s.strip()]
    return []
