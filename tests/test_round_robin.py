from dataclasses import replace
from datetime import UTC, datetime

import pandas as pd
import pytest

from nfl_alt_lines import baseline, strategy
from nfl_alt_lines.db.schema import ROUND_ROBINS
from nfl_alt_lines.db.service import LedgerStore
from nfl_alt_lines.round_robin import (
    FLAGGED_ROLE,
    LEG_COLUMNS,
    afternoon,
    combo_return,
    game_multipliers,
    prop_multipliers,
    report,
    structure_name,
    structures,
    update,
)

SUNDAY_NOON = "2026-10-18T17:00:00+00:00"  # 12:00 PM Central
WEDNESDAY = datetime(2026, 10, 14, tzinfo=UTC)
SUNDAY_AFTER_KICKOFF = datetime(2026, 10, 18, 19, tzinfo=UTC)
BOOKS = ["draftkings", "fanduel"]
Row = dict[str, object]


def flagged_legs(priced: pd.DataFrame) -> pd.DataFrame:
    """A plain leg rule for testing the mechanics: each game's best-edge top-ranked line."""
    rows = priced[priced["bucket"] == "top"].assign(edge=priced["model"] - priced["break_even"])
    rows = rows.sort_values("edge", ascending=False).drop_duplicates("game_id")
    return rows.assign(role=FLAGGED_ROLE)[LEG_COLUMNS].reset_index(drop=True)


@pytest.fixture(autouse=True)
def mechanics_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The baseline, which never builds a round robin, with the plain leg rule above."""
    monkeypatch.setattr(strategy, "load", lambda: replace(baseline.STRATEGY, choose_legs=flagged_legs))


def line(game: str, book: str = "draftkings", price: int = 200, kickoff: str = SUNDAY_NOON, **extra: object) -> Row:
    """One priced, flagged under: a leg candidate."""
    return {"game_id": game, "game": game, "kickoff": kickoff, "book": book, "bet_type": "under", "side": "under",
            "bet": "Under", "line": 38.5, "shift": 8, "price": price, "break_even": 100 / (price + 100),
            "model": 0.36, "percentile": 0.9, "bucket": "top", **extra}  # fmt: skip


def prop(game: str, player: str, model: float, shape: float, market: str = "rec") -> Row:
    return {"game_id": game, "game": game, "kickoff": SUNDAY_NOON, "book": "draftkings", "market": market,
            "player": player, "player_id": f"id-{player}", "line": 60.5, "shift": 10, "price": 150,
            "break_even": 0.4, "model": model, "shape": shape}  # fmt: skip


def test_fair_coin_legs_break_even() -> None:
    table = structures([0.5, 0.5, 0.5], [2.0, 2.0, 2.0]).set_index("structure")
    assert table.loc["by 2s", "bets"] == 3
    assert table["expected_return_pct"].abs().max() == pytest.approx(0)
    assert list(table.index) == ["singles", "by 2s"]  # by 3s and 4s are not part of the plan
    assert table.loc["by 2s", "chance_of_nothing_pct"] == pytest.approx(50)  # fewer than two legs hit
    assert table.loc["by 2s", "all_hit_pays_x_stake"] == pytest.approx(4)


def test_one_leg_allows_only_singles() -> None:
    table = structures([0.6], [2.0])
    assert list(table["structure"]) == ["singles"]
    assert table["expected_return_pct"].iloc[0] == pytest.approx(20)
    assert table["chance_of_profit_pct"].iloc[0] == pytest.approx(60)


@pytest.mark.parametrize(
    ("multipliers", "size", "paid"),
    [
        ([3.0, 3.0, 0.0], 2, 3.0),  # one of three parlays pays 9
        ([3.0, 1.0, 0.0], 2, 1.0),  # a push leaves the other leg's odds
        ([2.0, 0.0, 1.0], 1, 1.0),  # singles: the average leg
    ],
    ids=["two-hits", "push", "singles"],
)
def test_combo_return_counts_only_parlays_that_fully_hit(multipliers: list[float], size: int, paid: float) -> None:
    assert combo_return(multipliers, size) == pytest.approx(paid)


@pytest.mark.parametrize(("size", "name"), [(1, "singles"), (2, "by 2s"), (3, "by 3s")], ids=["1", "2", "3"])
def test_structure_name(size: int, name: str) -> None:
    assert structure_name(size) == name


@pytest.mark.parametrize(
    ("kickoff", "used"),
    [
        ("2026-10-18T13:30:00+00:00", False),  # London, 8:30 AM Central
        ("2026-10-18T16:00:00+00:00", True),  # 11:00 AM, the earliest hour
        ("2026-10-18T21:59:00+00:00", True),  # 4:59 PM, the latest hour
        ("2026-10-19T00:20:00+00:00", False),  # Sunday night
        ("2026-10-17T17:00:00+00:00", False),  # Saturday noon
    ],
    ids=["london", "earliest", "latest", "sunday-night", "saturday"],
)
def test_afternoon_is_sunday_late_morning_to_late_afternoon_central(kickoff: str, used: bool) -> None:
    local = pd.Series(pd.to_datetime([kickoff], utc=True)).dt.tz_convert("America/Chicago")
    assert bool(afternoon(local).iloc[0]) is used


def test_round_robin_goes_to_the_book_with_the_strongest_legs(ledgers: LedgerStore) -> None:
    ledger = pd.DataFrame([line(g, "draftkings", 180) for g in "ABC"] + [line(g, "fanduel", 220) for g in "ABC"])
    saved = update(ledger, BOOKS, WEDNESDAY)
    assert set(saved["book"]) == {"fanduel"}
    assert len(saved) == 3
    assert set(saved["slate"]) == {"2026-10-18"}
    # Once the first leg has kicked off the pick is frozen, even if another book later pays more.
    later = ledger.assign(price=ledger["price"].where(ledger["book"] == "fanduel", 400))
    saved = update(later, BOOKS, SUNDAY_AFTER_KICKOFF)
    assert set(saved["book"]) == {"fanduel"}
    stored = ledgers.load(ROUND_ROBINS)
    pd.testing.assert_frame_equal(stored[saved.columns], saved, check_dtype=False)


@pytest.mark.parametrize(
    ("second_run", "rebuilt"),
    [
        (datetime(2026, 10, 18, 15, 15, tzinfo=UTC), True),  # 10:15 AM Central: the last run that may change it
        (datetime(2026, 10, 18, 15, 52, tzinfo=UTC), False),  # 10:52 AM: after the 10:15 email it is final
    ],
    ids=["ten-fifteen-run", "after-the-lock"],
)
def test_a_round_robin_is_rebuilt_until_it_locks(ledgers: LedgerStore, second_run: datetime, rebuilt: bool) -> None:
    """2026-10-18: a 10:52 AM run with fresh nflverse data changed two legs after the 10:15 email went out.
    The round robin is now final 95 minutes before its first leg."""
    update(pd.DataFrame([line(g, "draftkings", 180) for g in "ABC"]), BOOKS, WEDNESDAY)
    saved = update(pd.DataFrame([line(g, "fanduel", 220) for g in "ABCD"]), BOOKS, second_run)
    assert (set(saved["book"]), len(saved)) == (({"fanduel"}, 4) if rebuilt else ({"draftkings"}, 3))


@pytest.mark.parametrize(
    "ledger",
    [
        pd.DataFrame([line(g, kickoff="2026-12-19T21:30:00+00:00") for g in "ABC"]),  # Saturday: not a slate
        pd.DataFrame([line(g) for g in "AB"]),  # fewer than MIN_LEGS games
        pd.DataFrame([line(g, "bovada") for g in "ABC"]),  # a book not asked for
        pd.DataFrame(),
    ],
    ids=["saturday", "too-few-legs", "other-book", "empty-ledger"],
)
def test_no_round_robin_without_enough_sunday_legs(ledgers: LedgerStore, ledger: pd.DataFrame) -> None:
    assert update(ledger, BOOKS, WEDNESDAY).empty


def schedule(home: float | None, away: float | None = 0.0) -> pd.DataFrame:
    return pd.DataFrame([{"game_id": g, "home_score": home, "away_score": away} for g in "ABC"])


@pytest.mark.parametrize(
    ("home", "away", "total", "result"),
    [(30.0, 1.0, 38.5, 3.0), (39.0, 1.0, 38.5, 0.0), (38.0, 1.0, 39.0, 1.0), (None, None, 38.5, None)],
    ids=["under-wins", "under-loses", "push", "not-final"],
)
def test_game_legs_settle_on_final_scores(
    home: float | None, away: float | None, total: float, result: float | None
) -> None:
    """Multipliers: decimal odds on a win (+200 pays 3), 1 on a push, 0 on a loss; None until every game is final."""
    legs = pd.DataFrame([line(g, line=total) for g in "ABC"])
    expected = None if result is None else [result] * 3
    assert game_multipliers(schedule(home, away))(legs) == expected


def test_prop_legs_settle_on_player_stats() -> None:
    legs = pd.DataFrame([prop("A", "Ann", 0.5, 0.5), prop("A", "Ari", 0.5, 0.5), prop("A", "Al", 0.5, 0.5)])
    stats = pd.DataFrame(
        [
            {"game_id": "A", "player_id": "id-Ann", "receiving_yards": 75.0},  # over 60.5: paid at +150
            {"game_id": "A", "player_id": "id-Ari", "receiving_yards": 20.0},  # under: lost
        ]  # Al has no stat line: did not play, the leg is void
    )
    assert prop_multipliers(stats)(legs) == [2.5, 0.0, 1.0]
    assert prop_multipliers(stats.iloc[:0])(legs) is None  # the game has not finished


def saved_legs(prices: tuple[int, ...] = (200, 200, 200)) -> pd.DataFrame:
    rows = [line(g, price=p, slate="2026-10-18") for g, p in zip("ABC", prices, strict=True)]
    return pd.DataFrame(rows).assign(role="worth a look")


def test_live_report_shows_legs_structures_and_the_replay_note() -> None:
    text = report(saved_legs(), game_multipliers(schedule(None)), BOOKS, WEDNESDAY)
    assert text.startswith("SUNDAY ROUND ROBIN DETAILS: every leg at one book, the best of draftkings, fanduel")
    assert "Sunday Oct 18 at draftkings (may still change until the first leg kicks off):" in text
    assert "Under 38.5" in text and "Sun 12:00 PM" in text
    assert "Highest expected return: by 2s at +16.6% of the stake, which is above break-even" in text
    assert text.endswith(strategy.load().replay_note)
    frozen = report(saved_legs(), game_multipliers(schedule(None)), BOOKS, SUNDAY_AFTER_KICKOFF)
    assert "(final, first leg has kicked off)" in frozen


def test_whole_live_report_is_unchanged() -> None:
    """The report is emailed hourly: pin one round robin's text exactly."""
    text = report(saved_legs((150, 200, 250)), game_multipliers(schedule(None)), ["fanduel"], WEDNESDAY)
    assert text.split("\n")[:12] == [
        "SUNDAY ROUND ROBIN DETAILS: every leg at one book, the best of fanduel (one leg per game; stakes in the plan)",
        "",
        "Sunday Oct 18 at draftkings (may still change until the first leg kicks off):",
        "game      kickoff        bet  shift  price  needs_pct  model_pct  percentile         role",
        "   A Sun 12:00 PM Under 38.5      8    150       40.0       36.0        90.0 worth a look",
        "   B Sun 12:00 PM Under 38.5      8    200       33.3       36.0        90.0 worth a look",
        "   C Sun 12:00 PM Under 38.5      8    250       28.6       36.0        90.0 worth a look",
        "",
        "structure  bets  expected_return_pct  chance_of_profit_pct  chance_of_nothing_pct  all_hit_pays_x_stake",
        "  singles     3                  8.0                  44.3                   26.2                   3.0",
        "    by 2s     3                 15.6                  29.5                   70.5                   8.9",
        "Highest expected return: by 2s at +15.6% of the stake, which is above break-even by the model's own numbers.",
    ]


def test_finished_round_robins_are_totalled() -> None:
    text = report(saved_legs(), game_multipliers(schedule(30.0, 1.0)), BOOKS, SUNDAY_AFTER_KICKOFF)
    assert "None yet: it needs 3 priced Sunday games with a 'worth a look' bet." in text
    assert "Finished ones, one unit per bet (a handful of these is noise):" in text
    assert "Latest (2026-10-18): 3 of 3 legs hit." in text
    assert (
        "by 2s                 1       3  27.0       800.0\nsingles               1       3   9.0       200.0" in text
    )


def test_prop_report_has_its_own_title_and_caution() -> None:
    legs = pd.DataFrame([prop(g, f"P{g}", 0.5, 0.5) for g in "ABC"]).assign(slate="2026-10-18", role="prop lean")
    no_stats = pd.DataFrame(columns=["game_id", "player_id", "receiving_yards"])
    text = report(legs, prop_multipliers(no_stats), BOOKS, WEDNESDAY, props=True)
    assert text.startswith("PAPER PROPS-ONLY ROUND ROBIN at one book, the best of draftkings, fanduel")
    assert "PA over 60.5 rec yds" in text
    assert text.endswith("The prop model has not been tested against real prices, so that figure is probably too high.")
    empty = report(legs.iloc[:0], prop_multipliers(no_stats), BOOKS, WEDNESDAY, props=True)
    assert empty.endswith("None yet: it needs positive-edge props in 3 games on the same day.")


def test_update_writes_to_the_ledger_store_in_use_at_call_time(ledgers: LedgerStore) -> None:
    """Regression: `update(path=LEDGER)` once fixed the location when the module loaded, so a redirected test
    still wrote the checkout's real ledger. The store is now looked up on each call."""
    update(pd.DataFrame([line(g, "fanduel", 220) for g in "ABC"]), BOOKS, WEDNESDAY)
    assert len(ledgers.load(ROUND_ROBINS)) == 3
