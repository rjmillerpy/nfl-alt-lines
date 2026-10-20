from datetime import UTC, datetime
from importlib.metadata import EntryPoint, entry_points

import pandas as pd
import pytest

from nfl_alt_lines import baseline, strategy
from nfl_alt_lines.baseline import STRATEGY, BaseRates, WeeklyBaseRates, bucket, early_ranks, residuals

Row = dict[str, object]


def game(season: int, week: int, result: float, total: float, spread: float = 3.0, line: float = 44.0) -> Row:
    """A played game; `spread` is positive when the home team is favoured (nflverse), `result` the home margin."""
    return {"season": season, "week_": week, "result": result, "total": total, "spread_line": spread,
            "total_line_": line}  # fmt: skip


def test_residuals_are_how_far_each_side_beat_its_line() -> None:
    played = pd.DataFrame([game(2020, 1, result=10, total=40), game(2020, 2, result=-6, total=50, spread=-3)])
    found = residuals(played)
    assert list(found["favorite"]) == [7, 3]  # home -3 won by 10; away -3 won by 6
    assert list(found["underdog"]) == [-7, -3]
    assert list(found["over"]) == [-4, 6] and list(found["under"]) == [4, -6]


def test_a_line_is_priced_at_how_often_it_would_have_hit() -> None:
    played = pd.DataFrame([game(2020, w, result=3 + w, total=44) for w in range(1, 11)])  # favourite beat by 1..10
    rates = BaseRates(played)
    assert rates.probability("favorite", pd.DataFrame(), 6) == (0.4, 0.4)  # beat by 7, 8, 9 and 10
    assert rates.probability("favorite", pd.DataFrame(), 10) == (0.0, 0.0)  # beating it by exactly 10 is a push
    assert rates.percentile("under", pd.DataFrame()) == 0.5


def test_a_line_with_no_history_is_priced_never_to_be_bet() -> None:
    no_games = pd.DataFrame([game(2020, 1, result=0, total=0)]).iloc[:0]
    assert BaseRates(no_games).probability("over", pd.DataFrame(), 6) == (0.0, 0.0)


def test_base_rates_use_earlier_weeks_only() -> None:
    history = pd.DataFrame([game(2020, 1, result=20, total=44), game(2020, 2, result=-20, total=44)])
    before_week_2 = WeeklyBaseRates(history).before(2020, 2)
    assert before_week_2.probability("favorite", pd.DataFrame(), 6) == (1.0, 1.0)  # week 2's loss is not seen


@pytest.mark.parametrize(("percentile", "expected"), [(0.1, "skip"), (0.5, "middle"), (0.9, "top")])
def test_buckets(percentile: float, expected: str) -> None:
    assert bucket(percentile) == expected


def test_early_ranks_give_every_coming_game_no_opinion() -> None:
    schedule = pd.DataFrame(
        {"game_id": ["soon", "later", "no-lines"], "gameday": ["2026-10-18", "2026-11-30", "2026-10-18"],
         "gametime": ["13:00", "13:00", "13:00"], "away_team": ["CHI"] * 3, "home_team": ["ATL"] * 3,
         "spread_line": [3.0, 3.0, None]}
    )  # fmt: skip
    ranks = early_ranks(schedule, schedule, datetime(2026, 10, 15, tzinfo=UTC))
    assert list(ranks["game_id"].unique()) == ["soon"]
    assert set(ranks["bet_type"]) == set(STRATEGY.bet_types)
    assert set(ranks["percentile"]) == {0.5} and set(ranks["bucket"]) == {"middle"}


def test_the_baseline_never_bets() -> None:
    assert STRATEGY.recommendable == () and STRATEGY.evidence == {}
    assert baseline.no_legs(pd.DataFrame()).empty and baseline.no_prop_legs(pd.DataFrame()).empty
    ledger, stats = baseline.no_props(pd.DataFrame(), datetime(2026, 10, 15, tzinfo=UTC), refresh=False)
    assert ledger.empty and {"game", "market", "player_id"} <= set(ledger.columns)
    assert {"game_id", "player_id", "passing_yards"} <= set(stats.columns)


INSTALLED = sorted(entry_points(group=strategy.STRATEGY_GROUP), key=lambda point: point.name)


@pytest.mark.parametrize("point", INSTALLED, ids=[point.name for point in INSTALLED])
def test_every_installed_strategy_keeps_the_contract(point: EntryPoint) -> None:
    implementation: strategy.Strategy = point.load()
    assert set(implementation.bet_types) == {"favorite", "underdog", "over", "under"}
    assert set(implementation.recommendable) <= set(implementation.bet_types)
    assert set(implementation.evidence) == set(implementation.recommendable)
    assert [implementation.bucket(p) for p in (0.1, 0.5, 0.9)] == ["skip", "middle", "top"]
    stakes = implementation.staking
    assert stakes.single_stake > 0 and 0 < stakes.sunday_budget <= 0.05 and 0 < stakes.singles_share < 1
    assert implementation.min_legs >= 2 and implementation.standing_rules
