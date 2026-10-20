"""The public baseline strategy (strategy.py): historical base rates, no model and no opinion.

A line moved `shift` points against the bettor is priced at how often that would have hit in earlier games:
the share of past games whose result beat their own closing line by more than `shift`. Every game sits at the
50th percentile for every bet type, so nothing is ever worth a look and nothing is bet. The whole pipeline
(logging, pricing, the ledger, grading, CLV, the emails) runs on it; the production strategy is private.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from nfl_alt_lines.odds.books import PRIMARY_BOOK
from nfl_alt_lines.odds.snapshots import LoggedGame
from nfl_alt_lines.prop_markets import YARDS
from nfl_alt_lines.round_robin import LEG_COLUMNS, PROP_COLUMNS
from nfl_alt_lines.strategy import Staking, Strategy

BET_TYPES = ("favorite", "underdog", "over", "under")
FIRST_SEASON = 1999  # base rates need only results and closing lines: every season nflverse has them for
NO_OPINION = 0.5
NO_HISTORY = 0.0
BOTTOM, TOP = 0.2, 0.8
EARLY_DAYS = 8
SCHEDULE_ZONE = "America/New_York"  # the schedule lists kickoffs in Eastern time
EARLY_COLUMNS = ["game_id", "game", "kickoff", "bet_type", "percentile", "bucket"]
PROP_LEDGER_COLUMNS = [
    "game_id", "game", "kickoff", "snapshot", "book", "market", "player", "player_id", "line", "market_line",
    "shift", "price", "break_even", "projection", "model", "shape", "others_implied", "other_books", "season",
    "week", "recorded", "recent_games", "recent_hits", "history",
]  # fmt: skip


def residuals(games: pd.DataFrame) -> dict[str, pd.Series]:
    """Per bet type, by how many points each played game beat its own line (positive = the bet won)."""
    played = games.dropna(subset=["result", "spread_line", "total_line_"])
    spread = played[played["spread_line"] != 0]
    favourite_margin = np.where(spread["spread_line"] > 0, spread["result"], -spread["result"])
    favourite = pd.Series(favourite_margin - spread["spread_line"].abs(), index=spread.index)
    total = played["total"] - played["total_line_"]
    return {"favorite": favourite, "underdog": -favourite, "over": total, "under": -total}


class BaseRates:
    """How often a line moved `shift` points against the bettor hit, in the games it was given."""

    def __init__(self, history: pd.DataFrame) -> None:
        self._residuals = {bet: np.sort(values.to_numpy()) for bet, values in residuals(history).items()}

    def percentile(self, bet: str, game: pd.DataFrame) -> float:  # noqa: ARG002 - no opinion on any game
        return NO_OPINION

    def probability(self, bet: str, game: pd.DataFrame, shift: float) -> tuple[float, float]:  # noqa: ARG002
        values = self._residuals[bet]
        rate = float(np.mean(values > shift)) if len(values) else NO_HISTORY  # unknown: priced to never be bet
        return rate, rate


class WeeklyBaseRates:
    """Base rates per (season, week) from earlier weeks only, so a game is priced as it would have been then."""

    def __init__(self, history: pd.DataFrame) -> None:
        self._history = history

    def before(self, season: int, week: int) -> BaseRates:
        history = self._history
        earlier = (history["season"] < season) | ((history["season"] == season) & (history["week_"] < week))
        return BaseRates(history[earlier])


def bucket(percentile: float) -> str:
    if percentile <= BOTTOM:
        return "skip"
    return "top" if percentile > TOP else "middle"


def _features(everything: pd.DataFrame, schedule: pd.DataFrame, refresh: bool) -> pd.DataFrame:  # noqa: ARG001
    return schedule  # base rates need nothing beyond the schedule's results and closing lines


def frame(schedule: pd.DataFrame, features: pd.DataFrame, games: list[LoggedGame]) -> pd.DataFrame:  # noqa: ARG001
    """The schedule, with each logged game's lines taken from the primary book (as the ledger prices them)."""
    table = schedule.copy()
    for logged in games:
        spreads, total = logged.lines.get(PRIMARY_BOOK) or next(iter(logged.lines.values()))
        table.loc[logged.row, ["spread_line", "total_line"]] = [-spreads[logged.event.home_team], total]
    return table.assign(total_line_=table["total_line"], week_=table["week"])


def early_ranks(schedule: pd.DataFrame, frame: pd.DataFrame, now: datetime) -> pd.DataFrame:  # noqa: ARG001
    """Every game of the coming week with lines, at the 50th percentile for every bet type."""
    kickoff = pd.to_datetime(schedule["gameday"] + " " + schedule["gametime"], errors="coerce")
    kickoff = kickoff.dt.tz_localize(SCHEDULE_ZONE).dt.tz_convert("UTC")
    soon = schedule[schedule["spread_line"].notna() & (kickoff > now) & (kickoff <= now + timedelta(days=EARLY_DAYS))]
    rows = [
        {"game_id": game.game_id, "game": f"{game.away_team} @ {game.home_team}",
         "kickoff": kickoff[index].isoformat(), "bet_type": bet, "percentile": NO_OPINION, "bucket": "middle"}
        for index, game in zip(soon.index, soon.itertuples(), strict=True)
        for bet in BET_TYPES
    ]  # fmt: skip
    return pd.DataFrame(rows, columns=EARLY_COLUMNS)


def no_legs(priced: pd.DataFrame) -> pd.DataFrame:  # noqa: ARG001 - the baseline never builds a round robin
    return pd.DataFrame(columns=LEG_COLUMNS)


def no_prop_legs(priced: pd.DataFrame) -> pd.DataFrame:  # noqa: ARG001
    return pd.DataFrame(columns=PROP_COLUMNS)


def no_props(everything: pd.DataFrame, now: datetime, refresh: bool) -> tuple[pd.DataFrame, pd.DataFrame]:  # noqa: ARG001
    """No prop model: an empty prop ledger, and no player stats to grade it with."""
    return pd.DataFrame(columns=PROP_LEDGER_COLUMNS), pd.DataFrame(columns=["game_id", "player_id", *YARDS.values()])


STRATEGY = Strategy(
    name="baseline",
    version="baseline-1",
    holdout_season=FIRST_SEASON,
    first_feature_season=FIRST_SEASON,
    bet_types=BET_TYPES,
    recommendable=(),
    evidence={},
    staking=Staking(sunday_budget=0.02, singles_share=0.5, single_stake=0.01),
    min_legs=3,
    replay_note="The baseline strategy has no backtest: it never builds a round robin.",
    standing_rules=(
        "Baseline strategy: every line is priced at its historical base rate, with no model and no opinion,"
        " so nothing is flagged and nothing is bet.",
    ),
    glance_notes=(
        "The baseline strategy ranks every game at the 50th percentile: no game is a stay away or worth a look.",
    ),
    features=_features,
    frame=frame,
    rankers=WeeklyBaseRates,
    early_ranks=early_ranks,
    bucket=bucket,
    choose_legs=no_legs,
    choose_prop_legs=no_prop_legs,
    props=no_props,
)
