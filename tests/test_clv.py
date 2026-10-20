from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest

from nfl_alt_lines.alt_lines import fair_probabilities
from nfl_alt_lines.clv import CLOSE_COLUMNS, add_closing, closing_values, clv_headline
from nfl_alt_lines.odds.schema import Event
from nfl_alt_lines.pricing import closing_line_value
from tests.odds.payloads import KICKOFF, event, outcome

pytestmark = pytest.mark.usefixtures("test_rules")  # tests/conftest.py: neutral rules, not production's

AFTER = datetime(2026, 10, 18, 18, tzinfo=UTC)
GAME = "2026_07_CHI_ATL"


def totals_book(key: str, over: int, under: int) -> dict[str, Any]:
    outcomes = [outcome("Over", over, 54.5), outcome("Under", under, 54.5)]
    return {"key": key, "markets": [{"key": "alternate_totals", "outcomes": outcomes}]}


def closing(*books: dict[str, Any]) -> Event:
    return Event.model_validate(event(bookmakers=list(books)))


def under_row(**changes: object) -> dict[str, object]:
    row = {"game_id": GAME, "kickoff": KICKOFF, "book": "draftkings", "bet_type": "under", "side": "under",
           "bet": "Under", "line": 54.5, "price": 250, "bucket": "top"}  # fmt: skip
    return {**row, **changes}


def test_closing_values_use_the_no_vig_consensus_of_every_book() -> None:
    # The bet's own book closed at +240; both books' margin-free probabilities are averaged.
    game = closing(totals_book("draftkings", -300, 240), totals_book("fanduel", -280, 220))
    dk_fair = (1 / 3.4) / (1 / 3.4 + 0.75)
    fd_fair = (1 / 3.2) / (1 / 3.2 + 280 / 380)
    values = closing_values(pd.Series(under_row()), game, fair_probabilities(game))
    assert values is not None
    fair, price, books, clv = values
    assert fair == pytest.approx((dk_fair + fd_fair) / 2)
    assert (price, books) == (240, 2)
    assert clv == pytest.approx(closing_line_value(250, fair))


def test_closing_price_is_missing_when_only_other_books_kept_the_line() -> None:
    game = closing(totals_book("fanduel", -280, 220))
    values = closing_values(pd.Series(under_row()), game, fair_probabilities(game))
    assert values is not None
    assert np.isnan(values[1])


def test_add_closing_fills_started_games_only() -> None:
    game = {GAME: closing(totals_book("draftkings", -300, 240))}
    rows = pd.DataFrame([under_row(), under_row(kickoff="2026-10-25T17:00:00Z"), under_row(line=55.5)])
    out = add_closing(rows, game, AFTER)
    assert list(out.columns[-4:]) == CLOSE_COLUMNS
    assert out["clv"].notna().tolist() == [True, False, False]  # filled, not started, line gone at the close


def test_add_closing_keeps_values_already_filled() -> None:
    rows = pd.DataFrame([under_row(close_fair=0.2, close_price=230.0, close_books=3.0, clv=0.1)])
    out = add_closing(rows, {GAME: closing(totals_book("draftkings", -300, 240))}, AFTER)
    assert out["clv"].iloc[0] == 0.1


def test_add_closing_on_an_empty_ledger_adds_the_columns() -> None:
    assert list(add_closing(pd.DataFrame(), {}, AFTER).columns) == CLOSE_COLUMNS


def test_clv_headline() -> None:
    done = pd.DataFrame([under_row(clv=0.10), under_row(bucket="skip", clv=-0.20)])
    assert clv_headline(done) == (
        "CLV, expected return at the no-vig closing consensus: all lines -5.0% (n=2), top-bucket plays +10.0% (n=1)."
    )
    assert clv_headline(done.assign(clv=np.nan)).startswith("CLV: no closing prices yet")
