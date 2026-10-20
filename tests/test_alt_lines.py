import math
from pathlib import Path

import pandas as pd
import pytest

from nfl_alt_lines import strategy
from nfl_alt_lines.alt_lines import MAX_SHIFT, MIN_SHIFT, fair_probabilities, price_rows
from nfl_alt_lines.odds.schema import Event
from nfl_alt_lines.odds.snapshots import LoggedGame, logged_games
from nfl_alt_lines.pricing import break_even
from nfl_alt_lines.strategy import Ranker
from tests import synthetic_slate

HOME, AWAY = "Atlanta Falcons", "Chicago Bears"


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> tuple[list[LoggedGame], pd.DataFrame]:
    raw: Path = tmp_path_factory.mktemp("odds")
    schedule = synthetic_slate.schedule()
    logged = logged_games(schedule, synthetic_slate.write_snapshots(raw))
    return logged, strategy.load().frame(schedule, schedule, logged)


@pytest.fixture(scope="module")
def ranker(world: tuple[list[LoggedGame], pd.DataFrame]) -> Ranker:
    _, frame = world
    return strategy.load().rankers(frame[frame["result"].notna()]).before(2015, 1)  # the slate's upcoming week


@pytest.fixture(scope="module")
def priced(
    world: tuple[list[LoggedGame], pd.DataFrame], ranker: Ranker
) -> tuple[LoggedGame, pd.DataFrame, list[dict[str, object]]]:
    logged, frame = world
    chi_atl = next(game for game in logged if game.event.home_team == HOME)
    game = frame.loc[[chi_atl.row]]
    return chi_atl, game, price_rows(chi_atl, game, ranker)


def _event(books: tuple[str, ...] = synthetic_slate.BOOKS) -> Event:
    return Event.model_validate(synthetic_slate.event(HOME, AWAY, "e", books))


def test_fair_probabilities_remove_each_books_margin() -> None:
    fair = fair_probabilities(_event())
    assert set(fair) == set(synthetic_slate.BOOKS)
    home, away = (
        fair["draftkings"][("alternate_spreads", HOME, -10.0)],
        fair["draftkings"][("alternate_spreads", AWAY, 10.0)],
    )
    assert home + away == pytest.approx(1)
    assert home == pytest.approx(break_even(250) / (break_even(250) + break_even(-330)))
    over, under = (
        fair["draftkings"][("alternate_totals", "Over", 51.0)],
        fair["draftkings"][("alternate_totals", "Under", 51.0)],
    )
    assert over + under == pytest.approx(1)


def test_fair_probabilities_skip_a_line_without_its_opposite() -> None:
    event = synthetic_slate.event(HOME, AWAY, "e", ("draftkings",))
    spreads = event["bookmakers"][0]["markets"][2]
    spreads["outcomes"] = [o for o in spreads["outcomes"] if not (o["name"] == AWAY and o["point"] == 10.0)]
    fair = fair_probabilities(Event.model_validate(event))
    assert ("alternate_spreads", HOME, -10.0) not in fair["draftkings"]
    assert ("alternate_spreads", HOME, -14.0) in fair["draftkings"]


def test_price_rows_keep_only_lines_6_to_21_points_against_the_bettor(
    priced: tuple[LoggedGame, pd.DataFrame, list[dict[str, object]]],
) -> None:
    _, _, rows = priced
    shifts = {float(str(row["shift"])) for row in rows}
    assert shifts == {7.0, 11.0}
    assert all(MIN_SHIFT <= shift <= MAX_SHIFT for shift in shifts)
    # Per book: favorite at 7 and 11, underdog at 7, over 51 and under 37 (both 7); the 4- and 14-point
    # opposites move the line in the bettor's favor and are left out.
    assert len(rows) == 5 * len(synthetic_slate.BOOKS)


def test_price_rows_describe_each_line(priced: tuple[LoggedGame, pd.DataFrame, list[dict[str, object]]]) -> None:
    logged, game, rows = priced
    first = rows[0]
    assert first["game_id"] == game["game_id"].iloc[0]
    assert first["game"] == "CHI @ ATL"
    assert first["kickoff"] == "2015-09-13T17:00:00+00:00"
    assert first["snapshot"] == synthetic_slate.STAMP
    assert (first["book"], first["bet_type"], first["side"], first["bet"]) == ("draftkings", "favorite", "home", HOME)
    assert (first["line"], first["shift"]) == (-10.0, 7.0)
    assert first["bucket"] in ("skip", "middle", "top")
    by_type = {(row["book"], row["bet_type"], row["shift"]): row for row in rows}
    assert by_type[("draftkings", "underdog", 7.0)]["side"] == "away"
    assert by_type[("draftkings", "over", 7.0)]["line"] == 51.0
    assert by_type[("draftkings", "under", 7.0)]["line"] == 37.0
    assert logged.lines["draftkings"] == ({HOME: -3.0, AWAY: 3.0}, 44.0)


def test_price_rows_keep_american_odds_whole(priced: tuple[LoggedGame, pd.DataFrame, list[dict[str, object]]]) -> None:
    """Regression guard for the refactor: the validated schema reads prices as floats, but the ledger CSV and
    the report print them as the API sent them (186, not 186.0), so whole prices are stored as int."""
    _, _, rows = priced
    assert all(type(row["price"]) is int for row in rows)
    assert rows[0]["price"] == 250
    assert rows[0]["break_even"] == pytest.approx(break_even(250))


def test_price_rows_average_the_other_books_fair_price(
    priced: tuple[LoggedGame, pd.DataFrame, list[dict[str, object]]],
) -> None:
    logged, _, rows = priced
    fair = fair_probabilities(logged.event)
    key = ("alternate_spreads", HOME, -10.0)
    first = rows[0]
    assert first["other_books"] == 2
    assert first["market_fair"] == pytest.approx((fair["fanduel"][key] + fair["betmgm"][key]) / 2)


def test_price_rows_need_the_books_main_line_and_two_other_books(
    priced: tuple[LoggedGame, pd.DataFrame, list[dict[str, object]]], ranker: Ranker
) -> None:
    logged, game, _ = priced
    only_draftkings = {"draftkings": logged.lines["draftkings"]}
    two_books = LoggedGame(_event(("draftkings", "fanduel")), logged.kickoff, logged.stamp, only_draftkings, logged.row)
    rows = price_rows(two_books, game, ranker)
    assert {row["book"] for row in rows} == {"draftkings"}
    assert all(row["other_books"] == 1 for row in rows)
    assert all(math.isnan(float(str(row["market_fair"]))) for row in rows)
