"""Log sportsbook prices for NFL games that kick off soon.

DraftKings only posts its full alternate-line ladder close to kickoff, so each game is snapshotted once
inside a window before it starts. A second, closing snapshot of the alternate spreads and totals is taken at
the last hourly run before kickoff, so each play can be compared with the closing price (CLV).

Line movement (roadmap item 29): every MOVEMENT_EVERY the main spreads and totals of every upcoming game are
saved too, at every book (2 credits a time, about 480 a month), so a season of how lines move all week builds
up. They go to their own folder, which the hourly report never reads, and to the logged prices table.

Run hourly; the events lookup is free and a run with nothing due spends no credits.
"""

import argparse
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import TypeAdapter

from nfl_alt_lines import run_status
from nfl_alt_lines.common import http_client
from nfl_alt_lines.common.logs import configure_logging
from nfl_alt_lines.common.settings import ENV_FILE, load_settings
from nfl_alt_lines.common.storage import Storage, open_storage
from nfl_alt_lines.db import service
from nfl_alt_lines.db.service import LedgerStore, add_prices
from nfl_alt_lines.odds.client import COMMON_PARAMS, Fetch, OddsClient
from nfl_alt_lines.odds.prices import price_rows
from nfl_alt_lines.odds.schema import Event
from nfl_alt_lines.odds.snapshots import (
    CLOSING,
    EVENTS,
    MAIN,
    MAIN_MARKETS,
    MOVEMENT,
    by_event,
    snapshot_key,
    stamp_time,
)

EVENT_MARKETS = (
    "alternate_spreads",
    "alternate_totals",
    "player_pass_yds_alternate",
    "player_rush_yds_alternate",
    "player_reception_yds_alternate",
)
WINDOW_HOURS = 6
CLOSE_MARKETS = ("alternate_spreads", "alternate_totals")  # one credit each per game
CLOSE_WINDOW = timedelta(hours=1)  # the last run before kickoff: cron fires hourly
CLOSE_RESERVE = 50  # closing snapshots never spend the last credits a pricing snapshot may need
MOVEMENT_EVERY = timedelta(hours=3)
MOVEMENT_SLACK = timedelta(minutes=10)  # runs start a little late; a 3-hour gap measured as 2:59 still counts
# Line movement is nice to have: it only runs while more than this is left, so it never spends what pricing
# snapshots need, and never runs at all on the free tier (500 credits a month).
MOVEMENT_RESERVE = 1000
EVENT_LIST = TypeAdapter(tuple[Event, ...])

log = logging.getLogger(__name__)


class MissingKeyError(Exception):
    def __init__(self) -> None:
        super().__init__("ODDS_API_KEY is not set in .env or the environment")


class NotEnoughCreditsError(Exception):
    def __init__(self, needed: int, remaining: int) -> None:
        super().__init__(f"not enough credits: need {needed}, have {remaining}")


def save(store: Storage, folder: str, now: datetime, body: bytes) -> None:
    """Store a response as received, wrapped with the time it was fetched (the snapshot format)."""
    payload = b'{"fetched_at": "' + now.isoformat().encode() + b'", "response": ' + body + b"}"
    store.write(snapshot_key(folder, now), payload)


Logged = dict[str, list[str]]  # event id -> its snapshot keys (snapshots.by_event)


def already_logged(keys: list[str], window_start: datetime) -> bool:
    return any(stamp_time(key) >= window_start for key in keys)


def due_events(events: tuple[Event, ...], now: datetime, window: timedelta, logged: Logged) -> list[Event]:
    """Games kicking off within `window` that have no snapshot since the window opened."""
    return [
        event
        for event in events
        if now < event.commence_time <= now + window
        and not already_logged(logged.get(event.id, []), event.commence_time - window)
    ]


def credit_cost(due: list[Event], markets: list[str]) -> int:
    """The most a run can spend: one main-lines call plus one call per due game, each priced per market."""
    return len(MAIN_MARKETS) + len(due) * len(markets) if due else 0


def closing_cost(closing: list[Event]) -> int:
    """Closing snapshots need no main-lines call: one call per game, priced per closing market."""
    return len(closing) * len(CLOSE_MARKETS)


def _game(event: Event) -> str:
    return f"{event.away_team} @ {event.home_team}"


@dataclass(frozen=True)
class Sinks:
    """Where a run's results go: the raw snapshot files, and one row per price in the database."""

    files: Storage
    prices: LedgerStore


def _keep(sinks: Sinks, folder: str, now: datetime, body: bytes, events: tuple[Event, ...]) -> None:
    save(sinks.files, folder, now, body)
    run_status.count("rows_written", add_prices(sinks.prices, price_rows(events, now)))


def _snapshot_due(client: OddsClient, sinks: Sinks, now: datetime, due: list[Event], markets: list[str]) -> int:
    """Main lines plus each due game's markets; returns the credits left."""
    body, remaining = client.get("/odds", markets=",".join(MAIN_MARKETS), **COMMON_PARAMS)
    _keep(sinks, MAIN, now, body, EVENT_LIST.validate_json(body))
    for event in due:
        body, remaining = client.get(f"/events/{event.id}/odds", markets=",".join(markets), **COMMON_PARAMS)
        priced = Event.model_validate_json(body)
        _keep(sinks, f"{EVENTS}/{event.id}", now, body, (priced,))
        books = len(priced.bookmakers)
        log.info("game logged", extra={"game": _game(event), "books": books, "credits_remaining": remaining})
    return remaining


def _snapshot_closing(client: OddsClient, sinks: Sinks, now: datetime, closing: list[Event], remaining: int) -> int:
    """Closing alternate spreads and totals for games about to kick off, unless that would eat the reserve;
    returns the credits left."""
    cost = closing_cost(closing)
    if cost > remaining - CLOSE_RESERVE:
        log.warning("closing skipped", extra={"close_cost": cost, "credits_remaining": remaining})
        return remaining
    for event in closing:
        body, remaining = client.get(f"/events/{event.id}/odds", markets=",".join(CLOSE_MARKETS), **COMMON_PARAMS)
        _keep(sinks, f"{CLOSING}/{event.id}", now, body, (Event.model_validate_json(body),))
        log.info("closing logged", extra={"game": _game(event), "credits_remaining": remaining})
    return remaining


def movement_due(keys: list[str], now: datetime) -> bool:
    """Whether the last line-movement snapshot is at least MOVEMENT_EVERY old (or there is none)."""
    latest = max((stamp_time(key) for key in keys), default=None)
    return latest is None or now - latest >= MOVEMENT_EVERY - MOVEMENT_SLACK


def _snapshot_movement(client: OddsClient, sinks: Sinks, now: datetime, remaining: int) -> None:
    """Main lines of every upcoming game, when one is due and the credits allow it."""
    if not movement_due(sinks.files.keys(MOVEMENT), now):
        return
    if len(MAIN_MARKETS) > remaining - MOVEMENT_RESERVE:
        log.warning("line movement skipped", extra={"credits_remaining": remaining})
        return
    body, remaining = client.get("/odds", markets=",".join(MAIN_MARKETS), **COMMON_PARAMS)
    events = EVENT_LIST.validate_json(body)
    _keep(sinks, MOVEMENT, now, body, events)
    log.info("line movement logged", extra={"games": len(events), "credits_remaining": remaining})


@dataclass(frozen=True)
class Run:
    """One logger run: when, how far ahead, which markets, and whether to spend anything."""

    now: datetime
    window: timedelta
    markets: list[str]
    dry_run: bool = False
    movement: bool = False  # also take a line-movement snapshot when one is due (the hourly job does)


def log_prices(client: OddsClient, store: Storage, run: Run, prices: LedgerStore | None = None) -> int:
    """Snapshot every due game, then the closing lines of games about to start; returns how many were priced."""
    now, markets = run.now, run.markets
    sinks = Sinks(store, prices or service.default_ledgers())
    body, remaining = client.get("/events")
    events = EVENT_LIST.validate_json(body)
    due = due_events(events, now, run.window, by_event(store, EVENTS))
    closing = due_events(events, now, CLOSE_WINDOW, by_event(store, CLOSING))
    cost = credit_cost(due, markets)
    counts = {"due": len(due), "max_cost": cost, "close_cost": closing_cost(closing), "credits_remaining": remaining}
    log.info("prices due", extra=counts)
    run_status.count("games_priced", 0)
    run_status.count("rows_written", 0)
    if run.dry_run:
        return 0
    if cost > remaining:
        raise NotEnoughCreditsError(cost, remaining)
    if due:
        remaining = _snapshot_due(client, sinks, now, due, markets)
        run_status.count("games_priced", len(due))
    remaining = _snapshot_closing(client, sinks, now, closing, remaining)
    if run.movement and events:
        _snapshot_movement(client, sinks, now, remaining)
    return len(due)


def main(
    argv: list[str] | None = None,
    env_file: Path = ENV_FILE,
    fetch: Fetch = http_client.get,
    store: Storage | None = None,
) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hours", type=float, default=WINDOW_HOURS, help="log games kicking off within this many hours"
    )
    parser.add_argument("--markets", default=",".join(EVENT_MARKETS))
    parser.add_argument("--dry-run", action="store_true", help="show what would be pulled; spends nothing")
    parser.add_argument("--no-movement", action="store_true", help="skip the line-movement snapshot")
    args = parser.parse_args(argv)
    configure_logging("odds-logger")
    settings = load_settings(env_file)
    if settings.odds_api_key is None:
        raise MissingKeyError
    client = OddsClient(settings.odds_api_key, fetch=fetch)
    run = Run(
        datetime.now(UTC), timedelta(hours=args.hours), args.markets.split(","), args.dry_run, not args.no_movement
    )
    log_prices(client, store or open_storage(settings), run)


if __name__ == "__main__":
    main()
