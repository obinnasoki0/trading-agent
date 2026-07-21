"""Market data providers.

Three sources, in order of preference for real use:

* ``YFinanceData``  -- free historical/daily bars via the ``yfinance`` package.
* ``CSVData``       -- load your own OHLCV CSVs (date,open,high,low,close,volume).
* ``SyntheticData`` -- a geometric-Brownian-motion generator so the whole
                       project runs and backtests with **no network and no
                       credentials**. Great for smoke tests and demos.

Every provider returns a pandas DataFrame indexed by timestamp with columns
open/high/low/close/volume, so strategies and the backtester never care where
the data came from.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd


def _stable_seed(text: str) -> int:
    """Process-independent seed. Builtin hash() is salted per run, which would
    make 'reproducible' backtests silently non-reproducible."""
    digest = hashlib.sha256(text.encode()).digest()
    return int.from_bytes(digest[:4], "big")


class DataProvider:
    def history(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        raise NotImplementedError


class SyntheticData(DataProvider):
    """Deterministic-ish random walk. Seeded per-symbol for reproducibility."""

    def __init__(self, start_price: float = 100.0, annual_vol: float = 0.25, annual_drift: float = 0.08):
        self.start_price = start_price
        self.annual_vol = annual_vol
        self.annual_drift = annual_drift

    def history(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        days = max(1, (end - start).days)
        rng = np.random.default_rng(_stable_seed(symbol))

        dt = 1 / 252
        mu, sigma = self.annual_drift, self.annual_vol
        shocks = rng.normal((mu - 0.5 * sigma**2) * dt, sigma * np.sqrt(dt), days)
        closes = self.start_price * np.exp(np.cumsum(shocks))

        intraday = np.abs(rng.normal(0, sigma * np.sqrt(dt) * closes, days))
        opens = np.concatenate([[self.start_price], closes[:-1]])
        highs = np.maximum(opens, closes) + intraday
        lows = np.minimum(opens, closes) - intraday
        volume = rng.integers(1_000_000, 5_000_000, days).astype(float)

        idx = pd.date_range(start=start, periods=days, freq="B")
        return pd.DataFrame(
            {"open": opens, "high": highs, "low": np.maximum(lows, 0.01),
             "close": closes, "volume": volume},
            index=idx,
        )


class CSVData(DataProvider):
    def __init__(self, directory: str):
        self.directory = directory

    def history(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        path = os.path.join(self.directory, f"{symbol}.csv")
        df = pd.read_csv(path, parse_dates=[0], index_col=0)
        df.columns = [c.lower() for c in df.columns]
        return df.loc[str(start.date()): str(end.date())]


class YFinanceData(DataProvider):
    def __init__(self, interval: str = "1d"):
        self.interval = interval

    def history(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                "yfinance is not installed. Run `pip install yfinance` "
                "or use SyntheticData / CSVData instead."
            ) from exc

        df = yf.download(symbol, start=start, end=end, interval=self.interval,
                         progress=False, auto_adjust=True)
        if df.empty:
            raise RuntimeError(f"No data returned for {symbol}.")
        df.columns = [str(c[0]).lower() if isinstance(c, tuple) else str(c).lower()
                      for c in df.columns]
        return df[["open", "high", "low", "close", "volume"]]


def default_provider() -> DataProvider:
    """Pick a provider from the TRADING_DATA_SOURCE env var.

    Defaults to synthetic so nothing breaks without setup.
    """
    source = os.getenv("TRADING_DATA_SOURCE", "synthetic").lower()
    if source == "yfinance":
        return YFinanceData()
    if source == "csv":
        return CSVData(os.getenv("TRADING_DATA_DIR", "./data"))
    return SyntheticData()


def make_window(days_back: int) -> tuple[datetime, datetime]:
    end = datetime.now()
    return end - timedelta(days=days_back), end
