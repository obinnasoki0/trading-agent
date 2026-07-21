from trading_agent.brokers.robinhood_mcp import _auto_map


def test_auto_map_matches_documented_style_names():
    details = [
        ("get_account", "Get account balances and buying power"),
        ("get_positions", "List current stock positions"),
        ("get_quote", "Get the latest price for a symbol"),
        ("place_order", "Place an equity buy or sell order"),
        ("cancel_order", "Cancel an open order by id"),
        ("get_watchlist", "Read your watchlist"),
    ]
    m = _auto_map(details)
    assert m["account"] == "get_account"
    assert m["positions"] == "get_positions"
    assert m["quote"] == "get_quote"
    assert m["place_order"] == "place_order"
    assert m["cancel_order"] == "cancel_order"


def test_auto_map_picks_equity_over_option_tools():
    # Names confirmed from the live Robinhood MCP (July 2026): equity and option
    # variants coexist; the equities-only engine must bind the equity ones.
    details = [
        ("get_portfolio", "Portfolio value and buying power"),
        ("get_positions", "Current positions"),
        ("get_quotes", "Latest quotes for symbols"),
        ("place_equity_order", "Place an equity order"),
        ("place_option_order", "Place an option order"),
        ("cancel_equity_order", "Cancel an open equity order"),
        ("cancel_option_order", "Cancel an open option order"),
    ]
    m = _auto_map(details)
    assert m["place_order"] == "place_equity_order"
    assert m["cancel_order"] == "cancel_equity_order"
    assert m["positions"] == "get_positions"
    assert m["quote"] == "get_quotes"
    assert m["account"] == "get_portfolio"


def test_auto_map_handles_alternate_naming():
    details = [
        ("account_details", "Portfolio value and cash"),
        ("list_holdings", "Your holdings"),
        ("latest_trade_price", "Latest trade price for a ticker"),
        ("submit_trade", "Submit a trade order"),
        ("cancel_trade", "Cancel a pending trade"),
    ]
    m = _auto_map(details)
    assert m["account"] == "account_details"
    assert m["positions"] == "list_holdings"
    assert m["quote"] == "latest_trade_price"
    assert m["place_order"] == "submit_trade"
    assert m["cancel_order"] == "cancel_trade"
    # place_order must not be confused with the cancel tool
    assert m["place_order"] != m["cancel_order"]
