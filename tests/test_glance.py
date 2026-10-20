from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest

from nfl_alt_lines import strategy
from nfl_alt_lines.glance import at_a_glance, low_scoring, upcoming_report, weather_notes
from tests.conftest import TEST_RULES

pytestmark = pytest.mark.usefixtures("test_rules")  # tests/conftest.py: neutral rules, not production's

NOW = datetime(2026, 10, 14, tzinfo=UTC)
KICKOFF = "2026-10-18T17:00:00+00:00"
LEDGER_COLUMNS = ["game_id", "game", "kickoff", "book", "bet_type", "percentile", "bucket"]


def _early(rows: list[tuple[str, float, str]], game: str = "CHI @ ATL") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"game_id": game, "game": game, "kickoff": KICKOFF, "bet_type": bet, "percentile": pct, "bucket": flag}
            for bet, pct, flag in rows
        ]
    )


def _priced(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "game_id": "g",
        "game": "SEA @ DEN",
        "kickoff": KICKOFF,
        "book": "fanduel",
        "bet_type": "under",
        "side": "under",
        "bet": "Under",
        "line": 37.5,
        "shift": 7.0,
        "price": 250,
        "break_even": 0.29,
        "base": 0.25,
        "model": 0.33,
        "percentile": 0.9,
        "bucket": "top",
        "market_fair": np.nan,
        "other_books": 0,
    }
    return {**row, **overrides}


def test_only_the_strategys_recommendable_types_are_worth_a_look(monkeypatch: pytest.MonkeyPatch) -> None:
    unders_only = replace(TEST_RULES, recommendable=("under",), evidence={"under": " - test note"})
    monkeypatch.setattr(strategy, "load", lambda: unders_only)
    early = _early([("underdog", 0.97, "top"), ("under", 0.9, "top")])
    ledger = pd.DataFrame(columns=LEDGER_COLUMNS)
    text = at_a_glance(ledger, early, ["draftkings"], NOW, {}, {})
    assert "WORTH A LOOK: under (90)" in text
    assert "WORTH A LOOK: underdog" not in text
    assert "no opinion:   underdog (97)" in text
    assert "early line, prices arrive on game day" in text


def test_at_a_glance_shows_the_best_logged_price_weather_and_prop_leans() -> None:
    ledger = pd.DataFrame(
        [
            _priced(),
            _priced(book="draftkings", line=36.5, price=300, break_even=0.25, model=0.31),
            _priced(bet_type="favorite", side="home", bet="DEN", line=-10.5, price=220, model=0.3, bucket="top"),
            _priced(bet_type="over", side="over", bet="Over", line=51.5, percentile=0.1, bucket="skip"),
            _priced(bet_type="underdog", side="away", bet="SEA", line=-4.5, percentile=0.5, bucket="middle"),
        ]
    )
    leans = {"SEA @ DEN": ["Player A over 60.5 receiving yards"]}
    text = at_a_glance(ledger, _early([]).reindex(columns=LEDGER_COLUMNS), ["fanduel", "draftkings"], NOW, leans,
                       {"g": "50F, wind 14 mph (windy)"})  # fmt: skip
    assert "SEA @ DEN  (Sun Oct 18, 12:00 PM)  50F, wind 14 mph (windy)" in text
    assert "STAY AWAY:    over (10)" in text
    # The draftkings under has the bigger model edge (6 points against 4) at a better price.
    assert "best price: Under 36.5 at +300 on draftkings (needs 25.0%, model 31.0%)" in text
    assert "best price: DEN -10.5 at +220 on fanduel" in text
    assert "no opinion:   underdog (50)" in text
    assert "PROP LEAN:    Player A over 60.5 receiving yards" in text
    assert "early line" not in text


def test_at_a_glance_with_no_games() -> None:
    empty = pd.DataFrame(columns=LEDGER_COLUMNS)
    text = at_a_glance(empty, empty.drop(columns="book"), ["fanduel"], NOW, {}, {})
    assert text.endswith("No upcoming games have lines yet.")


def test_at_a_glance_with_nothing_flagged() -> None:
    early = _early([("favorite", 0.5, "middle")])
    text = at_a_glance(pd.DataFrame(columns=LEDGER_COLUMNS), early, ["fanduel"], NOW, {}, {})
    assert "STAY AWAY:    nothing flagged" in text
    assert "WORTH A LOOK: nothing flagged" in text


def test_no_props_in_games_expected_to_score_little() -> None:
    def flag(game: str, bet_type: str, bucket: str) -> dict[str, Any]:
        return {"game_id": game, "game": game, "kickoff": KICKOFF, "bet_type": bet_type, "percentile": 0.5,
                "bucket": bucket}  # fmt: skip

    early = pd.DataFrame(
        [flag("UNDER", "under", "top"), flag("NO OVER", "over", "skip"), flag("OPEN", "under", "middle")]
    )
    # Once a game is priced its game-day rating wins over the early one.
    ledger = pd.DataFrame([flag("OPEN", "over", "middle"), flag("NOW UNDER", "under", "top")])
    early.loc[len(early)] = flag("NOW UNDER", "under", "middle")
    assert low_scoring(ledger, early, NOW) == {"UNDER", "NO OVER", "NOW UNDER"}


def test_book_with_only_spreads_posted_still_reports() -> None:
    ledger = pd.DataFrame(
        [
            _priced(book="betrivers", bet_type="favorite", side="home", bet="DEN", line=-10.5, percentile=0.6,
                    bucket="middle", model=0.27),
            _priced(book="betrivers", bet_type="underdog", side="away", bet="SEA", line=10.5, price=300,
                    break_even=0.25, model=0.2, percentile=0.3, bucket="middle"),
        ]
    )  # fmt: skip
    text = upcoming_report(ledger, "betrivers", NOW, 5)
    assert "SEA @ DEN" in text and "<NA>" in text
    assert text.endswith("none right now")


def test_upcoming_report_lists_lines_the_book_pays_more_on() -> None:
    ledger = pd.DataFrame(
        [
            _priced(market_fair=0.35, other_books=3),
            _priced(bet_type="over", bet="Over", side="over", line=51.5, market_fair=0.2, other_books=2),
        ]
    )
    text = upcoming_report(ledger, "fanduel", NOW, 5)
    market = text.split("fair price (needs 2+ other books):\n")[1]
    assert "Under  37.5" in market
    assert "51.5" not in market  # the book pays less than the others' fair price
    assert "Best edges at fanduel" in text


@pytest.mark.parametrize(
    ("book", "now"),
    [("caesars", NOW), ("fanduel", datetime(2026, 10, 19, tzinfo=UTC))],
    ids=["book-not-logged", "games-already-started"],
)
def test_upcoming_report_with_no_prices(book: str, now: datetime) -> None:
    text = upcoming_report(pd.DataFrame([_priced()]), book, now, 5)
    assert text.startswith("No prices logged yet for upcoming games")


@pytest.mark.parametrize(
    ("roof", "wind", "expected"),
    [
        ("outdoors", 15.0, "50F, wind 15 mph (windy)"),
        ("outdoors", 5.0, "50F, wind 5 mph"),
        ("dome", 15.0, None),
        ("outdoors", np.nan, None),
    ],
    ids=["windy", "calm", "indoors", "no-forecast"],
)
def test_weather_notes(roof: str, wind: float, expected: str | None) -> None:
    schedule = pd.DataFrame({"game_id": ["g"], "roof": [roof], "stadium_id": ["s"], "temp": [50.0], "wind": [wind]})
    assert weather_notes(schedule).get("g") == expected
