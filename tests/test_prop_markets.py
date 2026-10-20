from nfl_alt_lines.prop_markets import MAX_SHIFT, MIN_SHIFT, ODDS_MARKETS, YARDS


def test_every_market_has_an_odds_api_key_and_a_stat_column() -> None:
    assert set(YARDS) == set(ODDS_MARKETS) == {"pass", "rush", "rec"}
    assert all(key.startswith("player_") and key.endswith("_alternate") for key in ODDS_MARKETS.values())
    assert all(column.endswith("_yards") for column in YARDS.values())
    assert 0 < MIN_SHIFT < MAX_SHIFT
