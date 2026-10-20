import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from nfl_alt_lines.common.storage import LocalStorage
from nfl_alt_lines.db.models import PRICES
from nfl_alt_lines.db.service import LedgerStore
from nfl_alt_lines.odds import logger
from nfl_alt_lines.odds.client import OddsClient
from nfl_alt_lines.odds.logger import (
    CLOSE_MARKETS,
    EVENT_MARKETS,
    MissingKeyError,
    NotEnoughCreditsError,
    Run,
    already_logged,
    closing_cost,
    credit_cost,
    due_events,
    log_prices,
    movement_due,
    save,
)
from nfl_alt_lines.odds.schema import Event, EventSnapshot, SlateSnapshot
from nfl_alt_lines.odds.snapshots import CLOSING, EVENTS, MAIN, MOVEMENT, by_event, snapshot_key
from tests.odds.network import FakeOddsAPI
from tests.odds.payloads import event, main_book

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
WINDOW = timedelta(hours=6)
SOON, LATER = "2026-10-18T17:00:00Z", "2026-10-19T00:20:00Z"
CLOSING_SOON = "2026-10-18T12:55:00Z"  # inside the last hour before kickoff
MARKETS = ["alternate_spreads", "alternate_totals"]
RUN = Run(NOW, WINDOW, MARKETS)


@pytest.fixture
def store(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path)


def client(network: FakeOddsAPI) -> OddsClient:
    return OddsClient(SecretStr("k"), fetch=network)


def games(*kickoffs: str) -> tuple[Event, ...]:
    return tuple(Event.model_validate(event(f"e{i}", kickoff=k)) for i, k in enumerate(kickoffs))


def test_save_writes_the_snapshot_format(store: LocalStorage) -> None:
    save(store, "x", NOW, json.dumps(event()).encode())
    assert EventSnapshot.model_validate_json(store.read(snapshot_key("x", NOW))).response.id == "e1"


@pytest.mark.parametrize(
    ("snapshot_time", "expected"),
    [(NOW - timedelta(hours=1), True), (NOW - timedelta(hours=7), False)],
    ids=["inside-window", "before-window"],
)
def test_already_logged(snapshot_time: datetime, expected: bool) -> None:
    assert already_logged([snapshot_key("e1", snapshot_time)], NOW - WINDOW) is expected


def test_due_events_are_inside_the_window_and_not_yet_logged(store: LocalStorage) -> None:
    upcoming = games(SOON, LATER, "2026-10-18T11:00:00Z")  # in the window, too late, already started
    assert [e.id for e in due_events(upcoming, NOW, WINDOW, {})] == ["e0"]
    save(store, f"{EVENTS}/e0", NOW - timedelta(minutes=30), b"{}")
    assert due_events(upcoming, NOW, WINDOW, by_event(store, EVENTS)) == []


@pytest.mark.parametrize(("due", "expected"), [(0, 0), (1, 4), (3, 8)], ids=["nothing-due", "one-game", "three-games"])
def test_credit_cost(due: int, expected: int) -> None:
    assert credit_cost(list(games(*[SOON] * due)), MARKETS) == expected


def test_closing_cost() -> None:
    assert closing_cost(list(games(SOON, SOON))) == 2 * len(CLOSE_MARKETS)


def test_logs_main_lines_and_each_due_game(store: LocalStorage) -> None:
    payload = event("e0", kickoff=SOON, bookmakers=[main_book()])
    network = FakeOddsAPI(
        {"/events": [payload, event("e1", kickoff=LATER)], "/odds": [payload], "/events/e0/odds": payload}
    )
    assert log_prices(client(network), store, RUN) == 1
    assert network.paths == ["/events", "/odds", "/events/e0/odds"]
    assert network.requests[2][1]["markets"] == ",".join(MARKETS)
    assert store.keys(MAIN) == [snapshot_key(MAIN, NOW)]
    assert SlateSnapshot.model_validate_json(store.read(snapshot_key(MAIN, NOW))).response[0].id == "e0"
    assert store.exists(snapshot_key(f"{EVENTS}/e0", NOW))


def test_a_second_run_in_the_window_spends_and_writes_nothing(store: LocalStorage) -> None:
    # Idempotency (roadmap item 13): a retried or double-fired run is harmless.
    payload = event("e0", kickoff=SOON, bookmakers=[main_book()])
    network = FakeOddsAPI({"/events": [payload], "/odds": [payload], "/events/e0/odds": payload})
    log_prices(client(network), store, RUN)
    written = store.keys("raw")
    assert log_prices(client(network), store, Run(NOW + timedelta(minutes=1), WINDOW, MARKETS)) == 0
    assert network.paths == ["/events", "/odds", "/events/e0/odds", "/events"]
    assert store.keys("raw") == written


@pytest.mark.parametrize(("kickoff", "dry_run"), [(LATER, False), (SOON, True)], ids=["nothing-due", "dry-run"])
def test_spends_nothing_when_nothing_due_or_dry_run(store: LocalStorage, kickoff: str, dry_run: bool) -> None:
    network = FakeOddsAPI({"/events": [event(kickoff=kickoff)]})
    assert log_prices(client(network), store, Run(NOW, WINDOW, MARKETS, dry_run)) == 0
    assert network.paths == ["/events"]


def test_refuses_to_start_without_enough_credits(store: LocalStorage) -> None:
    network = FakeOddsAPI({"/events": [event(kickoff=SOON)]}, credits=3)
    with pytest.raises(NotEnoughCreditsError):
        log_prices(client(network), store, RUN)
    assert network.paths == ["/events"]


def test_takes_a_closing_snapshot_in_the_last_hour(store: LocalStorage) -> None:
    save(store, f"{EVENTS}/e0", NOW - timedelta(hours=4), b"{}")  # priced earlier
    payload = event("e0", kickoff=CLOSING_SOON)
    network = FakeOddsAPI({"/events": [payload], "/events/e0/odds": payload})
    assert log_prices(client(network), store, RUN) == 0
    assert network.paths == ["/events", "/events/e0/odds"]
    assert network.requests[1][1]["markets"] == ",".join(CLOSE_MARKETS)
    assert store.exists(snapshot_key(f"{CLOSING}/e0", NOW))
    # A second run in the same hour (a retry or a double-fired cron) spends nothing more.
    log_prices(client(network), store, Run(NOW + timedelta(minutes=2), WINDOW, MARKETS))
    assert network.paths == ["/events", "/events/e0/odds", "/events"]


def test_closing_never_spends_the_reserve(store: LocalStorage) -> None:
    save(store, f"{EVENTS}/e0", NOW - timedelta(hours=4), b"{}")
    network = FakeOddsAPI({"/events": [event("e0", kickoff=CLOSING_SOON)]}, credits=logger.CLOSE_RESERVE + 1)
    log_prices(client(network), store, RUN)
    assert network.paths == ["/events"]
    assert store.keys(CLOSING) == []


def test_main_without_a_key_stops(tmp_path: Path, store: LocalStorage) -> None:
    network = FakeOddsAPI({})
    with pytest.raises(MissingKeyError):
        logger.main(["--dry-run"], tmp_path / "absent", network, store)
    assert network.paths == []


def test_main_runs_a_dry_run(tmp_path: Path, store: LocalStorage) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ODDS_API_KEY=k\n")
    network = FakeOddsAPI({"/events": [event(kickoff=SOON)]})
    logger.main(["--dry-run", "--hours", "6"], env_file, network, store)
    assert network.paths == ["/events"]
    assert network.requests[0][1]["apiKey"] == "k"
    assert len(EVENT_MARKETS) == 5


def test_main_opens_the_configured_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ODDS_API_KEY=k\n")
    opened: list[object] = []

    def fake_open(settings: object) -> LocalStorage:
        opened.append(settings)
        return LocalStorage(tmp_path)

    monkeypatch.setattr(logger, "open_storage", fake_open)
    logger.main(["--dry-run"], env_file, FakeOddsAPI({"/events": []}))
    assert len(opened) == 1


def test_a_repeated_fetch_stores_each_price_once(tmp_path: Path, ledgers: LedgerStore) -> None:
    """Regression for roadmap item 13: a double-fired run that has lost track of its files fetches the same
    prices again; the unique key on (book update time, game, book, market, selection) keeps one row each."""
    book = main_book()
    book["markets"][0]["last_update"] = "2026-10-18T11:58:00Z"
    payload = event("e0", kickoff=SOON, bookmakers=[book])
    network = FakeOddsAPI({"/events": [payload], "/odds": [payload], "/events/e0/odds": payload})
    log_prices(client(network), LocalStorage(tmp_path / "first"), RUN, ledgers)
    with ledgers.engine.connect() as connection:
        stored = connection.execute(select(func.count()).select_from(PRICES)).scalar_one()
    log_prices(client(network), LocalStorage(tmp_path / "second"), RUN, ledgers)
    with ledgers.engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(PRICES)).scalar_one() == stored == 4


MOVING = Run(NOW, WINDOW, MARKETS, movement=True)
PAID_MONTH = 2500  # credits left on a paid plan; the free tier (500) is below the movement reserve


@pytest.mark.parametrize(
    ("last", "due"),
    [(None, True), (NOW - timedelta(hours=3), True), (NOW - timedelta(hours=2, minutes=55), True),
     (NOW - timedelta(hours=2), False)],
    ids=["never", "three-hours", "a-late-start", "two-hours"],
)  # fmt: skip
def test_movement_is_due_every_three_hours(last: datetime | None, due: bool) -> None:
    keys = [] if last is None else [snapshot_key(MOVEMENT, last)]
    assert movement_due(keys, NOW) is due


def test_line_movement_saves_every_games_main_lines_then_waits(store: LocalStorage, ledgers: LedgerStore) -> None:
    upcoming = event("e1", kickoff=LATER, bookmakers=[main_book()])
    network = FakeOddsAPI({"/events": [upcoming], "/odds": [upcoming]}, credits=PAID_MONTH)
    assert log_prices(client(network), store, MOVING) == 0  # nothing due for pricing
    assert network.paths == ["/events", "/odds"]
    assert network.requests[1][1]["markets"] == "spreads,totals"
    assert store.keys(MOVEMENT) == [snapshot_key(MOVEMENT, NOW)]
    assert store.keys(MAIN) == []  # the report reads every file in MAIN, so movement stays out of it
    with ledgers.engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(PRICES)).scalar_one() > 0
    log_prices(client(network), store, Run(NOW + timedelta(hours=1), WINDOW, MARKETS, movement=True))
    assert network.paths[2:] == ["/events"]  # an hour later: not due yet


@pytest.mark.parametrize(
    ("events", "credits"), [([], 500), (["e1"], logger.MOVEMENT_RESERVE + 1)], ids=["no-games", "reserve"]
)
def test_line_movement_spends_nothing_without_games_or_below_the_reserve(
    store: LocalStorage, events: list[str], credits: int
) -> None:
    network = FakeOddsAPI({"/events": [event(e, kickoff=LATER) for e in events]}, credits=credits)
    log_prices(client(network), store, MOVING)
    assert network.paths == ["/events"]
    assert store.keys(MOVEMENT) == []


@pytest.mark.parametrize(("flags", "calls"), [([], ["/events", "/odds"]), (["--no-movement"], ["/events"])])
def test_the_hourly_job_takes_movement_snapshots_unless_told_not_to(
    tmp_path: Path, store: LocalStorage, ledgers: LedgerStore, flags: list[str], calls: list[str]
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ODDS_API_KEY=k\n")
    upcoming = event("e1", kickoff=LATER, bookmakers=[main_book()])
    network = FakeOddsAPI({"/events": [upcoming], "/odds": [upcoming]}, credits=PAID_MONTH)
    logger.main(flags, env_file, network, store)
    assert network.paths == calls
