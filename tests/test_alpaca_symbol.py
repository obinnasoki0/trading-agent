"""Alpaca reports crypto positions without the slash ('LTCUSD'); we trade with
it ('LTC/USD'). If they don't match, the engine re-buys the same coin every
cycle (it never sees the holding) and never stops out. This guards the fix."""

from trading_agent.brokers.alpaca import _is_dust, _normalize_symbol, _round_qty


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


def test_dust_detection():
    # A crypto sell leaves sub-precision dust that can't be ordered. Anything
    # that rounds to 0 at 6 dp is dust; a real holding is not.
    assert _is_dust(0.0000003) is True       # rounds to 0.000000
    assert _is_dust(0.0000004) is True
    assert _is_dust(0.0) is True
    assert _is_dust(0.000001) is False       # exactly 1e-6 is tradable
    assert _is_dust(1_878_471_787.1685) is False  # a real SHIB holding
    assert _is_dust(0.15) is False


def test_round_qty_truncates_never_rounds_up():
    # Must floor, not round -- rounding a full-position sell UP past the held
    # amount makes Alpaca reject it as "insufficient balance".
    assert _round_qty(1.123456789) == 1.123456          # not 1.123457
    assert _round_qty(0.0000004) == 0.0
    # The real SHIB case: held ...955599 must never become ...956.
    assert _round_qty(1_927_646_557.367955599) <= 1_927_646_557.367955599
