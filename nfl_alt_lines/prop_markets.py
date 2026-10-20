"""The alternate yardage prop markets the app logs and grades: the Odds API market and the stat column that
settles it, by short name. Public data only; the prop model's own settings live with the strategy."""

ODDS_MARKETS = {
    "pass": "player_pass_yds_alternate",  # nosec B105 - "pass" is the passing market, not a password
    "rush": "player_rush_yds_alternate",
    "rec": "player_reception_yds_alternate",
}
YARDS = {"pass": "passing_yards", "rush": "rushing_yards", "rec": "receiving_yards"}  # nosec B105 - market names
# The lines logged and priced: every over 10-50 yards above the books' median line for the player.
MIN_SHIFT, MAX_SHIFT = 10, 50
