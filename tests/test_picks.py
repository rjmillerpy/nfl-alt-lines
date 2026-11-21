from dataclasses import replace

import pandas as pd
import pytest

from nfl_alt_lines import strategy
from nfl_alt_lines.picks import best_price, big_line_picks, flagged_picks, shop
from tests.conftest import TEST_RULES

pytestmark = pytest.mark.usefixtures("test_rules")  # tests/conftest.py: neutral rules, not production's


def row(line: float, book: str, price: int, model: float, shift: float, **extra: object) -> dict[str, object]:
    base = {"game_id": "G", "game": "G", "kickoff": "2026-10-16T00:15:00+00:00", "bet_type": "under", "bet": "Under",
            "bucket": "top", "percentile": 0.9}  # fmt: skip
    return {**base, "line": line, "book": book, "price": price, "break_even": 100 / (price + 100), "model": model,
            "shift": shift, **extra}  # fmt: skip


LADDER = pd.DataFrame(
    [
        row(38.5, "fanduel", 150, 0.6, 6.5),  # best edge: the ordinary single
        row(34.5, "fanduel", 380, 0.24, 10.5),
        row(34.5, "draftkings", 400, 0.24, 10.5),
        row(32.5, "fanduel", 500, 0.15, 12.5),  # below break-even
    ]
)


def test_the_flagged_pick_is_the_best_edge_line_at_the_book_paying_most() -> None:
    pick = flagged_picks(LADDER).iloc[0]
    assert (pick["line"], pick["book"], pick["price"]) == (38.5, "fanduel", 150)
    assert shop(LADDER, best_price(LADDER, LADDER.iloc[1])) == "best of 2 books; next best +380 on fanduel"


def test_bigger_lines_are_off_unless_the_strategy_stakes_them() -> None:
    assert big_line_picks(LADDER).empty


def test_the_bigger_line_skips_the_single_and_lines_below_break_even(monkeypatch: pytest.MonkeyPatch) -> None:
    rules = replace(TEST_RULES, staking=replace(TEST_RULES.staking, big_line_stake=0.002))
    monkeypatch.setattr(strategy, "load", lambda: rules)
    picks = big_line_picks(LADDER)
    assert picks[["line", "book", "price"]].values.tolist() == [[34.5, "draftkings", 400]]
