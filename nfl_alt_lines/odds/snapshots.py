"""Saved price snapshots: where they live in storage, reading them back (validated), and matching them to the
schedule. Keys look like `raw/odds/events/<event id>/<stamp>.json`."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

import pandas as pd

from nfl_alt_lines.common.storage import Storage
from nfl_alt_lines.odds.books import FEE_RATES, TEAM_CODES, book_name, net_price
from nfl_alt_lines.odds.schema import Event, EventSnapshot, Outcome, SlateSnapshot

RAW_PREFIX = "raw/odds"
EVENTS = f"{RAW_PREFIX}/events"  # one folder per game, one file per snapshot
MAIN = f"{RAW_PREFIX}/main"  # main lines for every upcoming game, one file per logger run
MOVEMENT = f"{RAW_PREFIX}/movement"  # main lines for every upcoming game every few hours (line movement)
CLOSING = f"{RAW_PREFIX}/closing"  # one folder per game: alternate lines at the last run before kickoff (CLV)
HISTORY = f"{RAW_PREFIX}/history"  # one file per past game, from the paid history download
TS_FORMAT = "%Y%m%dT%H%M%SZ"  # snapshot file names
MAIN_MARKETS = ("spreads", "totals")
SCHEDULE_MATCH_DAYS = 2  # a logged game matches a schedule row within this many days of its date

Markets = dict[str, dict[str, tuple[Outcome, ...]]]  # book -> market -> outcomes
MainLines = dict[str, tuple[dict[str, float], float]]  # book -> ({team: spread point}, total)


@dataclass(frozen=True)
class LoggedGame:
    """A game's latest pre-kickoff snapshot, the main lines it was priced off, and its schedule row."""

    event: Event
    kickoff: datetime
    stamp: str
    lines: MainLines
    row: int


def stamp_of(key: str) -> str:
    """The snapshot's file stamp, e.g. 20261018T120000Z."""
    return PurePosixPath(key).stem


def stamp_time(key: str) -> datetime:
    return datetime.strptime(stamp_of(key), TS_FORMAT).replace(tzinfo=UTC)


def snapshot_key(folder: str, now: datetime) -> str:
    return f"{folder}/{now.strftime(TS_FORMAT)}.json"


def by_event(store: Storage, prefix: str) -> dict[str, list[str]]:
    """Event id -> its snapshot keys, oldest first, from one listing of `prefix`."""
    found: defaultdict[str, list[str]] = defaultdict(list)
    for key in store.keys(prefix):
        found[PurePosixPath(key).parent.name].append(key)
    return dict(found)


def _after_fees(book: str, outcomes: tuple[Outcome, ...]) -> tuple[Outcome, ...]:
    if book not in FEE_RATES:
        return outcomes
    return tuple(outcome.model_copy(update={"price": net_price(book, outcome.price)}) for outcome in outcomes)


def markets(event: Event) -> Markets:
    """book -> market -> outcomes, at prices after any trading fee (the raw snapshot keeps the posted price)."""
    named = ((book_name(b.key), b) for b in event.bookmakers)
    return {book: {m.key: _after_fees(book, m.outcomes) for m in b.markets} for book, b in named}


def read_event(store: Storage, key: str) -> EventSnapshot:
    return EventSnapshot.model_validate_json(store.read(key))


def read_slates(store: Storage, prefix: str = MAIN) -> list[tuple[str, SlateSnapshot]]:
    """Every main-line snapshot with its file stamp, newest first. Read once per run, then searched."""
    keys = sorted(store.keys(prefix), reverse=True)
    return [(stamp_of(key), SlateSnapshot.model_validate_json(store.read(key))) for key in keys]


def _lines_at_books(event: Event) -> MainLines:
    lines = {}
    for book, offered in markets(event).items():
        if set(MAIN_MARKETS) <= offered.keys():
            spreads = {outcome.name: outcome.point for outcome in offered["spreads"]}
            lines[book] = (spreads, offered["totals"][0].point)
    return lines


def main_lines(event_id: str, stamp: str, slates: list[tuple[str, SlateSnapshot]]) -> MainLines:
    """Main lines from the newest snapshot taken no later than `stamp` that has this game.

    Later snapshots are ignored because they may hold in-play lines for a game that has started.
    """
    for slate_stamp, slate in slates:
        if slate_stamp > stamp:
            continue
        event = next((event for event in slate.response if event.id == event_id), None)
        lines = _lines_at_books(event) if event else {}
        if lines:
            return lines
    return {}


def _schedule_row(schedule: pd.DataFrame, event: Event) -> int | None:
    days = pd.to_datetime(schedule["gameday"])
    is_near = (days - pd.Timestamp(event.commence_time.date())).abs() <= pd.Timedelta(days=SCHEDULE_MATCH_DAYS)
    is_teams = (schedule["home_team"] == TEAM_CODES[event.home_team]) & (
        schedule["away_team"] == TEAM_CODES[event.away_team]
    )
    match = schedule.index[is_teams & is_near]
    return int(match[0]) if len(match) else None


def _closing_event(store: Storage, keys: list[str]) -> Event | None:
    """The newest snapshot taken before kickoff (later ones hold in-play prices)."""
    for key in reversed(keys):
        event = read_event(store, key).response
        if stamp_time(key) <= event.commence_time:
            return event
    return None


def closing_events(schedule: pd.DataFrame, store: Storage, prefix: str = CLOSING) -> dict[str, Event]:
    """game_id -> the closing snapshot (alternate spreads and totals) of each game that has one."""
    closing = {}
    for keys in by_event(store, prefix).values():
        event = _closing_event(store, keys)
        row = _schedule_row(schedule, event) if event else None
        if event and row is not None:
            closing[str(schedule["game_id"].loc[row])] = event
    return closing


def logged_games(schedule: pd.DataFrame, store: Storage, events: str = EVENTS, main: str = MAIN) -> list[LoggedGame]:
    """Each logged game's latest pre-kickoff snapshot, matched to its row in the schedule."""
    slates = read_slates(store, main)
    games = []
    for keys in by_event(store, events).values():
        event = read_event(store, keys[-1]).response
        row = _schedule_row(schedule, event)
        lines = main_lines(event.id, stamp_of(keys[-1]), slates)
        if row is not None and lines:
            games.append(LoggedGame(event, event.commence_time, stamp_of(keys[-1]), lines, row))
    return games
