"""Sportsbooks and team names as The Odds API spells them, and what this project calls them (rule 16)."""

from nfl_alt_lines.pricing import american_odds, break_even

PRIMARY_BOOK = "draftkings"
# The Illinois books with alternate lines in the Odds API "us" region, all in the same download.
BOOKS = ("draftkings", "fanduel", "betmgm", "caesars", "betrivers", "fanatics")
OLD_BOOKS = ("draftkings", "fanduel", "betmgm")  # the shopping set before Caesars, BetRivers and Fanatics
# Prediction-market exchanges (Kalshi: legal in Illinois; Novig: check the app): singles only, since they offer
# no round robin. Their posted price may only fill a small stake.
EXCHANGES = ("kalshi", "novig")
# Not licensed in any US state: singles only, and the email always names a licensed alternative.
OFFSHORE = ("bovada",)
SINGLES_BOOKS = (*BOOKS, *EXCHANGES, *OFFSHORE)  # every book a single may go to
BOOK_NAMES = {"williamhill_us": "caesars"}  # API keys renamed to what the app is called
# Kalshi's taker fee per $1 contract bought at price P is FEE_RATE x P x (1 - P); The Odds API's price leaves it
# out, so it is added here (Kalshi rounds each order's fee up to the cent, which this ignores). Novig's fee could
# not be confirmed when this was written (2026-10-19); none is assumed.
FEE_RATES = {"kalshi": 0.07}

TEAM_CODES = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR", "Chicago Bears": "CHI", "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL", "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX", "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC", "Los Angeles Rams": "LA", "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN", "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT", "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB", "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}  # fmt: skip


def book_name(api_key: str) -> str:
    return BOOK_NAMES.get(api_key, api_key)


def api_key(book: str) -> str:
    return next((key for key, name in BOOK_NAMES.items() if name == book), book)


def net_price(book: str, american: float) -> float:
    """The price after the book's trading fee, as American odds (whole numbers, like the books post)."""
    rate = FEE_RATES.get(book)
    if rate is None:
        return american
    win = break_even(american)
    return float(round(american_odds(1 / (win + rate * win * (1 - win)))))
