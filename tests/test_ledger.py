from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nfl_alt_lines import strategy
from nfl_alt_lines.db import service
from nfl_alt_lines.db.schema import PREDICTIONS
from nfl_alt_lines.db.service import LedgerStore
from nfl_alt_lines.ledger import (
    MIN_GRADED_GAMES,
    calibration_summary,
    graded,
    grading_summary,
    settle,
    update_ledger,
)
from nfl_alt_lines.odds.snapshots import LoggedGame, logged_games
from tests import synthetic_slate

AFTER_KICKOFF = datetime(2015, 9, 13, 18, tzinfo=UTC)


def _row(side: str, line: float, price: float = 250) -> pd.Series:
    return pd.Series({"side": side, "line": line, "price": price})


@pytest.mark.parametrize(
    ("side", "line", "price", "expected"),
    [
        ("home", -12.5, 250, 2.5),
        ("home", -13.5, 250, -1.0),
        ("home", -13.0, 250, 0.0),
        ("away", 13.5, -400, 0.25),
        ("away", 12.5, 250, -1.0),
        ("over", 46.5, 250, 2.5),
        ("over", 47.5, 250, -1.0),
        ("under", 47.5, 250, 2.5),
        ("under", 47.0, 250, 0.0),
    ],
    ids=[
        "home-covers",
        "home-misses",
        "home-push",
        "away-covers-at-short-odds",
        "away-misses",
        "over-hits",
        "over-misses",
        "under-hits",
        "under-push",
    ],
)
def test_settle_home_win_30_17(side: str, line: float, price: float, expected: float) -> None:
    assert settle(_row(side, line, price), 30, 17) == pytest.approx(expected)


@pytest.fixture
def world(tmp_path: Path, ledgers: LedgerStore) -> tuple[pd.DataFrame, pd.DataFrame, list[LoggedGame]]:
    schedule = synthetic_slate.schedule()
    games = logged_games(schedule, synthetic_slate.write_snapshots(tmp_path / "odds"))
    return schedule, strategy.load().frame(schedule, schedule, games), games


def test_update_ledger_records_every_logged_game_before_kickoff(
    world: tuple[pd.DataFrame, pd.DataFrame, list[LoggedGame]],
) -> None:
    schedule, frame, games = world
    ledger = update_ledger(schedule, frame, games, synthetic_slate.NOW)
    assert set(ledger["game"]) == {"CHI @ ATL", "CAR @ BUF"}
    assert set(ledger["recorded"]) == {"before kickoff"}
    assert set(ledger[["season", "week"]].itertuples(index=False, name=None)) == {(2015, 1)}
    saved = service.default_ledgers().load(PREDICTIONS)
    assert len(saved) == len(ledger)
    assert saved["price"].dtype == np.int64


def test_update_ledger_freezes_games_that_have_kicked_off(
    world: tuple[pd.DataFrame, pd.DataFrame, list[LoggedGame]],
) -> None:
    schedule, frame, games = world
    first = update_ledger(schedule, frame, games, synthetic_slate.NOW)
    again = update_ledger(schedule, frame, games, AFTER_KICKOFF)
    assert len(again) == len(first)  # kept from the file, not priced a second time
    assert set(again["recorded"]) == {"before kickoff"}


def test_update_ledger_records_a_game_first_seen_after_kickoff(
    world: tuple[pd.DataFrame, pd.DataFrame, list[LoggedGame]],
) -> None:
    schedule, frame, games = world
    ledger = update_ledger(schedule, frame, games, AFTER_KICKOFF)
    assert set(ledger["recorded"]) == {"after kickoff"}


def test_update_ledger_skips_games_without_lines(world: tuple[pd.DataFrame, pd.DataFrame, list[LoggedGame]]) -> None:
    schedule, frame, games = world
    frame = frame.copy()
    frame.loc[games[0].row, "total_line_"] = np.nan
    ledger = update_ledger(schedule, frame, games, synthetic_slate.NOW)
    assert ledger["game_id"].nunique() == 1


def _ledger(rows: int, bet_type: str = "under") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_id": [f"g{i}" for i in range(rows)],
            "book": "draftkings",
            "bet_type": bet_type,
            "side": "under" if bet_type == "under" else "home",
            "line": 40.5 if bet_type == "under" else -10.5,
            "price": 200,
            "break_even": 1 / 3,
            "base": 0.3,
            "model": 0.35,
            "bucket": ["skip", "middle", "top"] * (rows // 3) + ["top"] * (rows % 3),
        }
    )


def _scores(rows: int, home: float = 20, away: float = 17) -> pd.DataFrame:
    return pd.DataFrame({"game_id": [f"g{i}" for i in range(rows)], "home_score": home, "away_score": away})


def test_graded_settles_finished_games_only() -> None:
    scores = pd.concat([_scores(2), pd.DataFrame({"game_id": ["g2"], "home_score": [np.nan], "away_score": [np.nan]})])
    done = graded(_ledger(3), scores)
    assert list(done["game_id"]) == ["g0", "g1"]
    assert list(done["profit"]) == [pytest.approx(2.0)] * 2  # 37 points, under 40.5 at +200
    assert list(done["hit"]) == [1.0, 1.0]


def test_graded_marks_a_push_as_neither_hit_nor_miss() -> None:
    ledger = _ledger(1).assign(line=37.0)
    done = graded(ledger, _scores(1))
    assert done["profit"].iloc[0] == 0
    assert np.isnan(done["hit"].iloc[0])


def test_graded_with_nothing_finished_is_empty() -> None:
    assert graded(_ledger(3), _scores(3, home=np.nan, away=np.nan)).empty


def test_grading_summary_with_nothing_at_the_book() -> None:
    assert grading_summary(graded(_ledger(3), _scores(3)), "fanduel") == "No logged games have finished yet."


@pytest.mark.parametrize(
    ("games", "noisy"), [(3, True), (MIN_GRADED_GAMES, False)], ids=["few-games-are-noise", "enough-games"]
)
def test_grading_summary_tables_by_bucket(games: int, noisy: bool) -> None:
    text = grading_summary(graded(_ledger(games), _scores(games)), "draftkings")
    assert text.startswith(f"Graded so far at draftkings: {games} games, {games} alternate lines (clean holdout")
    assert ("treat every number below as noise" in text) is noisy
    assert "By ranker bucket" in text
    assert "Unders by bucket" in text
    assert "roi_pct" in text


def test_grading_summary_leaves_out_the_unders_table_without_unders() -> None:
    text = grading_summary(graded(_ledger(3, bet_type="favorite"), _scores(3)), "draftkings")
    assert "By ranker bucket" in text
    assert "Unders by bucket" not in text


def test_update_ledger_fills_closing_prices_once_games_start(
    world: tuple[pd.DataFrame, pd.DataFrame, list[LoggedGame]],
) -> None:
    schedule, frame, games = world
    first = update_ledger(schedule, frame, games, synthetic_slate.NOW)
    game_id = first["game_id"].iloc[0]
    closing = {game_id: games[0].event if first["game"].iloc[0] == "CHI @ ATL" else games[1].event}
    assert first["clv"].isna().all()  # not started yet
    after = update_ledger(schedule, frame, games, AFTER_KICKOFF, closing)
    filled = after[after["clv"].notna()]
    assert len(filled) and set(filled["game_id"]) == {game_id}
    assert service.default_ledgers().load(PREDICTIONS)["clv"].notna().sum() == len(filled)


def test_new_rows_record_the_model_version(world: tuple[pd.DataFrame, pd.DataFrame, list[LoggedGame]]) -> None:
    schedule, frame, games = world
    version = strategy.load().version
    assert set(update_ledger(schedule, frame, games, synthetic_slate.NOW)["model_version"]) == {version}


def test_grading_leaves_out_other_model_versions() -> None:
    version = strategy.load().version
    ledger = _ledger(3).assign(model_version=[version, version, "old"])
    text = grading_summary(graded(ledger, _scores(3)), "draftkings")
    assert "2 games, 2 alternate lines" in text
    assert "1 lines from earlier versions left out" in text


def test_grading_with_only_other_versions_is_empty() -> None:
    ledger = _ledger(3).assign(model_version="old")
    assert grading_summary(graded(ledger, _scores(3)), "draftkings") == "No logged games have finished yet."


def test_calibration_summary_scores_the_forecasts_that_are_there() -> None:
    done = graded(_ledger(3).assign(market_fair=[0.3, np.nan, 0.3]), _scores(3))
    text = calibration_summary(done)
    assert "model" in text and "base rate" in text
    assert "consensus" in text and "closing consensus" not in text
    assert "Reliability of the model" in text


def test_calibration_summary_without_settled_lines() -> None:
    done = graded(_ledger(1).assign(line=37.0), _scores(1))  # a push
    assert calibration_summary(done) == "Calibration: no settled lines yet."
