import time
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from nfl_alt_lines.prop_markets import MAX_SHIFT, MIN_SHIFT
from nfl_alt_lines.prop_report import (
    MIN_GRADED_GAMES,
    _yards,
    grading_summary,
    leans,
    upcoming_report,
)

FUTURE_KICKOFF = "2030-01-05T18:00:00+00:00"

NOW = datetime(2019, 11, 5, tzinfo=UTC)  # before FUTURE_KICKOFF, so those rows are upcoming


def _row(**values: object) -> dict[str, object]:
    """A ledger row with plain defaults: an upcoming +200 line at DraftKings that the model likes."""
    base: dict[str, object] = {
        "game_id": "g1", "game": "SEA @ DEN", "kickoff": FUTURE_KICKOFF, "book": "draftkings", "market": "rec",
        "player": "A Receiver", "player_id": "00-1", "line": 70.5, "market_line": 50.0, "shift": 20.5,
        "price": 200, "break_even": 1 / 3, "model": 0.45, "shape": 0.40, "history": 0.42, "others_implied": np.nan,
        "recent_games": 10, "recent_hits": 4,
    }  # fmt: skip
    return base | values


def _graded(games: int) -> pd.DataFrame:
    rows = [_row(game_id=f"g{k}", hit=float(k % 2), profit=2.0 if k % 2 else -1.0) for k in range(games)]
    rows.append(_row(game_id="g0", book="fanduel", hit=0.0, profit=-1.0, model=0.1, history=0.1))
    rows.append(_row(game_id="g0", market="rush", hit=0.0, profit=-1.0, model=0.1, history=0.1))
    return pd.DataFrame(rows).assign(edge=lambda d: d[["history", "shape"]].min(axis=1) - d["break_even"])


@pytest.mark.parametrize(
    ("done", "book"), [(pd.DataFrame(), "draftkings"), (_graded(2), "betmgm")], ids=["nothing-graded", "other-book"]
)
def test_grading_summary_without_finished_games(done: pd.DataFrame, book: str) -> None:
    assert grading_summary(done, book) == "Props: no logged games have finished yet."


@pytest.mark.parametrize(
    ("games", "warns"), [(MIN_GRADED_GAMES - 1, True), (MIN_GRADED_GAMES, False)], ids=["few-games", "enough-games"]
)
def test_grading_summary_counts_and_warns_about_small_samples(games: int, warns: bool) -> None:
    text = grading_summary(_graded(games), "draftkings")
    lines = text.splitlines()
    assert lines[0] == f"Props graded so far at draftkings: {games} games, 1 players, {games + 1} lines."
    assert ("treat every number below as noise" in text) == warns
    assert "edge above 0" in text
    assert "edge 0 or below" in text  # the rush line the model did not like


def test_upcoming_report_with_nothing_ahead() -> None:
    assert upcoming_report(pd.DataFrame(), "draftkings", NOW, 5) == "Props: no prices logged yet for upcoming games."
    past = pd.DataFrame([_row(kickoff="2019-01-01T00:00:00+00:00")])
    assert upcoming_report(past, "draftkings", NOW, 5) == "Props: no prices logged yet for upcoming games."


def test_upcoming_report_ranks_edges_and_cheaper_prices() -> None:
    ledger = pd.DataFrame(
        [
            _row(player="Alpha", shape=0.6, history=0.6, others_implied=0.40),
            _row(player="Beta", shape=0.5, history=0.5),
            _row(player="Gamma", shape=0.45, history=0.45),
        ]
    )
    text = upcoming_report(ledger, "draftkings", NOW, top=2)
    assert text.startswith(f"Best prop edges at draftkings (line {MIN_SHIFT}-{MAX_SHIFT} yards above")
    assert text.index("Alpha") < text.index("Beta")
    assert "Gamma" not in text
    assert "4 of 10" in text
    cheaper = text.split("pays more than the other books")[1]
    assert "Alpha" in cheaper
    assert "Beta" not in cheaper


def test_upcoming_report_when_nothing_can_be_priced() -> None:
    ledger = pd.DataFrame([_row(shape=np.nan)])
    text = upcoming_report(ledger, "draftkings", NOW, 5)
    assert "none priced" in text
    assert "none right now" in text


def test_rows_logged_before_the_history_check_use_the_model() -> None:
    ledger = pd.DataFrame([_row(model=0.5, shape=0.6)]).drop(columns=["history", "recent_games", "recent_hits"])
    text = upcoming_report(ledger, "draftkings", NOW, 5)
    assert "n/a" in text
    assert "16.7" in text  # edge: min(model 50%, shape 60%) - break-even 33.3%


def test_leans_lists_the_best_few_per_game_once_per_player() -> None:
    ledger = pd.DataFrame(
        [
            _row(player="Best", shape=0.6, history=0.6),
            _row(player="Best", line=80.5, shape=0.55, history=0.55),  # same player and market: only the best
            _row(player="Second", book="fanduel", price=150, break_even=0.4, shape=0.5, history=0.5),
            _row(player="No Edge", shape=0.2, history=0.2),
            _row(player="Other Book", book="betmgm", shape=0.9, history=0.9),
            _row(game="KC @ BUF", player="Elsewhere", shape=0.5, history=0.5),
        ]
    )
    out = leans(ledger, ["draftkings", "fanduel"], NOW, per_game=3)
    assert out == {
        "SEA @ DEN": [
            "Best over 70.5 rec yds at +200 on draftkings (needs 33.3%, model 60.0%, cleared it in 4 of 10)",
            "Second over 70.5 rec yds at +150 on fanduel (needs 40.0%, model 50.0%, cleared it in 4 of 10)",
        ],
        "KC @ BUF": [
            "Elsewhere over 70.5 rec yds at +200 on draftkings (needs 33.3%, model 50.0%, cleared it in 4 of 10)"
        ],
    }


@pytest.mark.parametrize(
    "ledger", [pd.DataFrame(), pd.DataFrame([_row(shape=0.1, history=0.1)])], ids=["empty", "no-edge"]
)
def test_leans_without_positive_edges(ledger: pd.DataFrame) -> None:
    assert leans(ledger, ["draftkings"], NOW) == {}


def test_grading_a_season_of_prop_lines_is_fast() -> None:
    """Performance regression: a lookup per line in the stats' unsorted index took a minute of each Fargate run
    once Sunday's props were graded (2026-10-19); one join grades 60,000 lines in well under a second."""
    players = [f"P{i}" for i in range(400)]
    stats = pd.DataFrame(
        [{"game_id": f"G{g}", "player_id": p, "passing_yards": 200.0, "rushing_yards": 40.0, "receiving_yards": 55.0}
         for g in range(50) for p in players]
    )  # fmt: skip
    lines = pd.DataFrame(
        {"game_id": stats["game_id"].repeat(3).to_numpy(), "player_id": stats["player_id"].repeat(3).to_numpy(),
         "market": ["pass", "rush", "rec"] * len(stats)}
    )  # fmt: skip
    start = time.perf_counter()
    yards = _yards(lines, stats)
    assert time.perf_counter() - start < 2
    assert list(yards[:3]) == [200.0, 40.0, 55.0]
