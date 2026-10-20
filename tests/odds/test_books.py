import pytest

from nfl_alt_lines.odds.books import (
    BOOKS,
    EXCHANGES,
    OFFSHORE,
    OLD_BOOKS,
    PRIMARY_BOOK,
    SINGLES_BOOKS,
    TEAM_CODES,
    api_key,
    book_name,
    net_price,
)
from nfl_alt_lines.odds.client import COMMON_PARAMS
from nfl_alt_lines.pricing import american_odds, decimal_odds


@pytest.mark.parametrize(
    ("api_key", "name"),
    [("williamhill_us", "caesars"), ("draftkings", "draftkings"), ("fanatics", "fanatics")],
    ids=["caesars-renamed", "draftkings-unchanged", "fanatics-unchanged"],
)
def test_book_name(api_key: str, name: str) -> None:
    assert book_name(api_key) == name


def test_book_lists_are_consistent() -> None:
    assert PRIMARY_BOOK in OLD_BOOKS
    assert set(OLD_BOOKS) <= set(BOOKS)
    assert len(set(BOOKS)) == len(BOOKS)


def test_every_team_has_a_unique_code() -> None:
    assert len(TEAM_CODES) == 32
    assert len(set(TEAM_CODES.values())) == 32


def test_singles_books_add_the_exchanges_and_bovada_in_one_billing_group() -> None:
    assert set(SINGLES_BOOKS) - set(BOOKS) == {"kalshi", "novig", "bovada"}
    assert not set(EXCHANGES + OFFSHORE) & set(BOOKS)  # round robins stay at the sportsbooks
    keys = COMMON_PARAMS["bookmakers"].split(",")
    assert len(keys) <= 10  # The Odds API bills each group of up to 10 books as one region
    assert "williamhill_us" in keys and "regions" not in COMMON_PARAMS


@pytest.mark.parametrize(("book", "key"), [("caesars", "williamhill_us"), ("kalshi", "kalshi")])
def test_api_key_undoes_the_rename(book: str, key: str) -> None:
    assert api_key(book) == key and book_name(key) == book


@pytest.mark.parametrize(
    ("book", "posted", "after_fees"),
    [("kalshi", 200, 187), ("kalshi", 100, -107), ("kalshi", -150, -161), ("novig", 200, 200), ("fanduel", 200, 200)],
    ids=["kalshi-underdog", "kalshi-even", "kalshi-favorite", "novig-no-fee", "sportsbook-no-fee"],
)
def test_net_price_takes_kalshis_fee_out(book: str, posted: int, after_fees: int) -> None:
    """+200 is a 33.3 cent contract; Kalshi's fee adds 0.07 x 0.333 x 0.667 = 1.56 cents, so it pays like +187."""
    assert net_price(book, posted) == after_fees


@pytest.mark.parametrize("american", [-250, -110, 100, 150, 340])
def test_american_odds_round_trips(american: int) -> None:
    assert american_odds(decimal_odds(american)) == pytest.approx(american)
