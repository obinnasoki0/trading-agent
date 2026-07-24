"""Alpaca reports crypto positions without the slash ('LTCUSD'); we trade with
it ('LTC/USD'). If they don't match, the engine re-buys the same coin every
cycle (it never sees the holding) and never stops out. This guards the fix."""

from trading_agent.brokers.alpaca import _normalize_symbol


def test_crypto_symbol_gets_slash_back():
    assert _normalize_symbol("LTCUSD", "crypto") == "LTC/USD"
    assert _normalize_symbol("BTCUSD", "crypto") == "BTC/USD"
    assert _normalize_symbol("ETHUSDT", "crypto") == "ETH/USDT"


def test_already_slashed_is_unchanged():
    assert _normalize_symbol("LTC/USD", "crypto") == "LTC/USD"


def test_equities_are_untouched():
    assert _normalize_symbol("AAPL", "us_equity") == "AAPL"
    assert _normalize_symbol("XOM", "us_equity") == "XOM"
    # A stock that happens to end in a quote-like string is not crypto -> unchanged.
    assert _normalize_symbol("PLUS", "us_equity") == "PLUS"
