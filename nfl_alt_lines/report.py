"""Weekly run: grade what has been played, then rank this week's logged alternate lines.

Every alternate line moved 6-21 points against the bettor is scored by the ranker (base rate
adjusted by all team stats, game context, quarterback and injuries, refitted each week on games already played).
Predictions are written to an append-only ledger and frozen at kickoff, so the grading
section is a genuine forward test of the two claims from the backtests:

- bet types the ranker puts in its bottom fifth hit less often than the base rate (skip them);
- unders the ranker puts in its top fifth hit more often.
"""

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from nfl_alt_lines import strategy
from nfl_alt_lines.common import storage
from nfl_alt_lines.common.logs import timed
from nfl_alt_lines.common.settings import load_settings
from nfl_alt_lines.db.schema import PREDICTIONS, PROP_ROUND_ROBINS
from nfl_alt_lines.glance import (
    at_a_glance,
    low_scoring,
    upcoming_report,
    weather_notes,
)
from nfl_alt_lines.ledger import graded, grading_summary, update_ledger
from nfl_alt_lines.nflverse import load_schedule
from nfl_alt_lines.odds.books import BOOKS, EXCHANGES, OFFSHORE
from nfl_alt_lines.odds.schema import Event
from nfl_alt_lines.odds.snapshots import TS_FORMAT, LoggedGame, closing_events, logged_games
from nfl_alt_lines.weather import add_weather

# plan.py and round_robin.py import these from here.

DEFAULT_TOP = 15
SECTION_RULE = "=" * 30


def _schedule(refresh: bool, now: datetime) -> pd.DataFrame:
    return add_weather(load_schedule(refresh=refresh), pd.Timestamp(now))


def _features(everything: pd.DataFrame, schedule: pd.DataFrame, refresh: bool) -> pd.DataFrame:
    return strategy.load().features(everything, schedule, refresh)


def _props(everything: pd.DataFrame, now: datetime, refresh: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    return strategy.load().props(everything, now, refresh)


def _games(schedule: pd.DataFrame) -> list[LoggedGame]:
    return logged_games(schedule, storage.default_storage())


def _closing(schedule: pd.DataFrame) -> dict[str, Event]:
    return closing_events(schedule, storage.default_storage())


def _bankroll() -> float | None:
    return load_settings().bankroll


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Sources:
    """Where the run's inputs come from (rule 58): nflverse, Open-Meteo, the snapshots (pricing and closing),
    .env and the clock."""

    schedule: Callable[[bool, datetime], pd.DataFrame] = _schedule
    features: Callable[[pd.DataFrame, pd.DataFrame, bool], pd.DataFrame] = _features
    games: Callable[[pd.DataFrame], list[LoggedGame]] = _games
    closing: Callable[[pd.DataFrame], dict[str, Event]] = _closing
    props: Callable[[pd.DataFrame, datetime, bool], tuple[pd.DataFrame, pd.DataFrame]] = _props
    bankroll: Callable[[], float | None] = _bankroll
    clock: Callable[[], datetime] = _now


LIVE = Sources()


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", nargs="+", default=list(BOOKS), help="one or more books, each gets a section")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument("--no-refresh", action="store_true", help="use the schedule and plays already on disk")
    return parser.parse_args(argv)


@dataclass(frozen=True)
class Week:
    """What the run has built before the report text: the schedules, the ranker's inputs and the ledger."""

    everything: pd.DataFrame  # every season, with kickoff weather
    schedule: pd.DataFrame  # the seasons the ranker uses
    frame: pd.DataFrame
    ledger: pd.DataFrame
    now: datetime


def _print_sections(week: Week, args: argparse.Namespace, sources: Sources) -> None:
    """Everything after the header line, in the order the email shows it."""
    from nfl_alt_lines import plan, prop_report, round_robin  # imported here: they build on this module

    ledger, schedule, now = week.ledger, week.schedule, week.now
    with timed("props"):
        prop_ledger, player_stats = sources.props(week.everything, now, not args.no_refresh)
    with timed("sections: grading"):
        prop_done = prop_report.graded(prop_ledger, player_stats)
        done = graded(ledger, schedule)
    with timed("sections: early ranks and leans"):
        early = strategy.load().early_ranks(schedule, week.frame, now)
        low = low_scoring(ledger, early, now)
        open_props = prop_ledger[~prop_ledger["game"].isin(low)]
        leans = prop_report.leans(open_props, args.book, now)
    with timed("sections: round robin"):
        round_robins = round_robin.update(ledger, args.book, now)
    # Singles may go to an exchange or Bovada as well; round robins stay at the sportsbooks in args.book.
    singles = list(dict.fromkeys([*args.book, *EXCHANGES, *OFFSHORE]))
    with timed("sections: plan and glance"):
        print(plan.plan(ledger, early, round_robins, singles, now, sources.bankroll()))
        print()
        print(at_a_glance(ledger, early, args.book, now, leans, weather_notes(schedule)))
        print()
        print(round_robin.report(round_robins, round_robin.game_multipliers(schedule), args.book, now))
        print()
    with timed("sections: prop round robin"):
        prop_robins = round_robin.update(
            open_props, args.book, now, PROP_ROUND_ROBINS, strategy.load().choose_prop_legs
        )
        print(round_robin.report(prop_robins, round_robin.prop_multipliers(player_stats), args.book, now, props=True))
        print()
    with timed("sections: books"):
        _print_books(args, done, ledger, prop_done, prop_ledger, now)


def _print_books(
    args: argparse.Namespace,
    done: pd.DataFrame,
    ledger: pd.DataFrame,
    prop_done: pd.DataFrame,
    prop_ledger: pd.DataFrame,
    now: datetime,
) -> None:
    from nfl_alt_lines import prop_report

    for book in args.book:
        print(f"{SECTION_RULE} {book.upper()} {SECTION_RULE}\n")
        print(grading_summary(done, book))
        print()
        print(upcoming_report(ledger, book, now, args.top))
        print()
        print(prop_report.grading_summary(prop_done, book))
        print()
        print(prop_report.upcoming_report(prop_ledger, book, now, args.top))
        print()


def main(argv: list[str] | None = None, sources: Sources = LIVE) -> None:
    args = _arguments(argv)
    now = sources.clock()
    refresh = not args.no_refresh
    with timed("schedule and weather"):
        everything = sources.schedule(refresh, now)
    rules = strategy.load()
    schedule = everything[everything["season"] >= rules.first_feature_season - 1].copy()
    with timed("features"):
        features = sources.features(everything, schedule, refresh)
    with timed("logged prices"):
        games = sources.games(schedule)
    with timed("frame"):
        frame = rules.frame(schedule, features, games)
    with timed("ledger"):
        ledger = update_ledger(schedule, frame, games, now, sources.closing(schedule))
    print(f"Run at {now.strftime(TS_FORMAT)}; ledger: the {PREDICTIONS.name} table\n")
    with timed("sections"):
        _print_sections(Week(everything, schedule, frame, ledger, now), args, sources)


if __name__ == "__main__":
    main()
