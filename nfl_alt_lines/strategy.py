"""The seam between the app and the model (docs/open-core-setup.md): everything the jobs need from the model,
the selection rules and the stakes, behind one object.

The app never imports the model's modules. It asks `load()` for the installed strategy: an implementation
registered under the entry point group STRATEGY_GROUP. The production strategy is preferred when installed;
otherwise the public baseline is used. STRATEGY (an environment variable) picks one by name.
"""

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from importlib.metadata import entry_points
from typing import Protocol

import pandas as pd

from nfl_alt_lines.odds.snapshots import LoggedGame

STRATEGY_GROUP = "nfl_alt_lines.strategies"
PREFERRED = ("production", "baseline")
STRATEGY_VARIABLE = "STRATEGY"


class Ranker(Protocol):
    """The model as of one week: how likely a line is to hit, and how the game ranks for a bet type."""

    def percentile(self, bet: str, game: pd.DataFrame) -> float: ...

    def probability(self, bet: str, game: pd.DataFrame, shift: float) -> tuple[float, float]:
        """(model, base rate) hit probability for a line moved `shift` points against the bettor."""
        ...


class Rankers(Protocol):
    """A ranker per (season, week), fitted on earlier weeks only: no lookahead."""

    def before(self, season: int, week: int) -> Ranker: ...


@dataclass(frozen=True)
class Staking:
    """Shares of the bankroll: the Sunday afternoon budget, the part of it bet as singles, other singles."""

    sunday_budget: float
    singles_share: float
    single_stake: float


@dataclass(frozen=True)
class Strategy:
    name: str
    version: str  # fingerprint of the settings that decide a ledger row's probability and bucket
    holdout_season: int  # the first season the strategy's settings were not tuned on
    first_feature_season: int
    bet_types: tuple[str, ...]
    recommendable: tuple[str, ...]  # bet types that may be shown as worth a look
    evidence: Mapping[str, str]  # a note per recommendable bet type, shown next to it
    staking: Staking
    min_legs: int  # a round robin needs this many legs
    replay_note: str  # how the round robin rule did in its backtest, shown under the round robin
    standing_rules: tuple[str, ...]  # lines under every plan: what never to bet, how sure the strategy is
    glance_notes: tuple[str, ...]  # lines under the at-a-glance section: caveats and what the evidence says
    features: Callable[[pd.DataFrame, pd.DataFrame, bool], pd.DataFrame]  # (every season, schedule, refresh)
    frame: Callable[[pd.DataFrame, pd.DataFrame, list[LoggedGame]], pd.DataFrame]  # the ranker's input table
    rankers: Callable[[pd.DataFrame], Rankers]  # from played games
    early_ranks: Callable[[pd.DataFrame, pd.DataFrame, datetime], pd.DataFrame]
    bucket: Callable[[float], str]  # "skip", "middle" or "top"
    choose_legs: Callable[[pd.DataFrame], pd.DataFrame]  # a round robin's legs from one book's priced lines
    choose_prop_legs: Callable[[pd.DataFrame], pd.DataFrame]  # the paper props-only round robin's legs
    props: Callable[[pd.DataFrame, datetime, bool], tuple[pd.DataFrame, pd.DataFrame]]  # (ledger, player stats)


class NoStrategyError(Exception):
    def __init__(self, wanted: tuple[str, ...]) -> None:
        super().__init__(f"no strategy installed under {STRATEGY_GROUP!r} named {' or '.join(wanted)}")


@cache
def load() -> Strategy:
    """The strategy the jobs use: STRATEGY if set, else production when installed, else the baseline."""
    found = {point.name: point for point in entry_points(group=STRATEGY_GROUP)}
    chosen = os.environ.get(STRATEGY_VARIABLE)
    wanted = (chosen,) if chosen else PREFERRED
    name = next((name for name in wanted if name in found), None)
    if name is None:
        raise NoStrategyError(wanted)
    strategy: Strategy = found[name].load()
    return strategy
