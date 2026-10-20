from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from nfl_alt_lines.common.storage import LocalStorage, Storage
from nfl_alt_lines.odds.schema import Event
from nfl_alt_lines.odds.snapshots import (
    CLOSING,
    EVENTS,
    MAIN,
    LoggedGame,
    by_event,
    closing_events,
    logged_games,
    main_lines,
    markets,
    read_slates,
    snapshot_key,
    stamp_of,
    stamp_time,
)
from tests.odds.payloads import event, main_book, snapshot

FETCHED = "2026-10-18T10:00:00Z"
SCHEDULE = pd.DataFrame(
    {"gameday": ["2026-10-18", "2026-10-18"], "home_team": ["ATL", "KC"], "away_team": ["CHI", "LAC"]},
    index=[7, 8],
)


@pytest.fixture
def store(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path)


def write(store: Storage, key: str, text: str) -> None:
    store.write(key, text.encode())


def test_markets_rename_books_and_group_by_market() -> None:
    game = Event.model_validate(event(bookmakers=[main_book("williamhill_us"), main_book("fanduel")]))
    found = markets(game)
    assert set(found) == {"caesars", "fanduel"}
    assert [o.point for o in found["caesars"]["totals"]] == [44.5, 44.5]


def test_markets_take_kalshis_fee_out_of_its_prices_only() -> None:
    game = Event.model_validate(event(bookmakers=[main_book("kalshi"), main_book("fanduel")]))
    found = markets(game)
    assert [o.price for o in found["kalshi"]["totals"]] == [-118, -118]  # -110 plus 1.7 cents of fee
    assert [o.price for o in found["fanduel"]["totals"]] == [-110, -110]
    assert game.bookmakers[0].markets[1].outcomes[0].price == -110  # the snapshot keeps the posted price


def test_keys_stamps_and_grouping(store: LocalStorage) -> None:
    key = snapshot_key(f"{EVENTS}/e1", datetime(2026, 10, 18, 12, tzinfo=UTC))
    assert key == f"{EVENTS}/e1/20261018T120000Z.json"
    assert stamp_of(key) == "20261018T120000Z"
    assert stamp_time(key) == datetime(2026, 10, 18, 12, tzinfo=UTC)
    write(store, key, "{}")
    write(store, f"{EVENTS}/e1/20261018T090000Z.json", "{}")
    assert by_event(store, EVENTS) == {"e1": [f"{EVENTS}/e1/20261018T090000Z.json", key]}


def test_read_slates_newest_first(store: LocalStorage) -> None:
    write(store, f"{MAIN}/20261018T100000Z.json", snapshot("2026-10-18T10:00:00Z", [event()]))
    write(store, f"{MAIN}/20261018T120000Z.json", snapshot("2026-10-18T12:00:00Z", [event()]))
    assert [stamp for stamp, _ in read_slates(store)] == ["20261018T120000Z", "20261018T100000Z"]


@pytest.mark.parametrize(
    ("stamp", "expected_spread"),
    [("20261018T130000Z", -4.5), ("20261018T110000Z", -3.5), ("20261018T090000Z", None)],
    ids=["latest-before-stamp", "skips-later-snapshot", "nothing-early-enough"],
)
def test_main_lines_use_newest_snapshot_not_after_stamp(
    store: LocalStorage, stamp: str, expected_spread: float | None
) -> None:
    write(store, f"{MAIN}/20261018T100000Z.json", snapshot(FETCHED, [event(bookmakers=[main_book(spread=-3.5)])]))
    write(store, f"{MAIN}/20261018T120000Z.json", snapshot(FETCHED, [event(bookmakers=[main_book(spread=-4.5)])]))
    lines = main_lines("e1", stamp, read_slates(store))
    if expected_spread is None:
        assert lines == {}
    else:
        assert lines["draftkings"] == ({"Atlanta Falcons": expected_spread, "Chicago Bears": -expected_spread}, 44.5)


def test_main_lines_skip_books_without_both_markets(store: LocalStorage) -> None:
    spreads_only = {"key": "fanduel", "markets": [main_book()["markets"][0]]}
    write(store, f"{MAIN}/20261018T100000Z.json", snapshot(FETCHED, [event(bookmakers=[spreads_only, main_book()])]))
    assert set(main_lines("e1", "20261018T120000Z", read_slates(store))) == {"draftkings"}


def test_main_lines_look_past_snapshots_missing_the_game(store: LocalStorage) -> None:
    write(store, f"{MAIN}/20261018T100000Z.json", snapshot(FETCHED, [event(bookmakers=[main_book()])]))
    write(store, f"{MAIN}/20261018T110000Z.json", snapshot(FETCHED, [event("other", bookmakers=[main_book()])]))
    assert "draftkings" in main_lines("e1", "20261018T120000Z", read_slates(store))


def test_logged_games_match_schedule_and_use_latest_snapshot(store: LocalStorage, tmp_path: Path) -> None:
    write(store, f"{MAIN}/20261018T100000Z.json", snapshot(FETCHED, [event(bookmakers=[main_book()])]))
    write(store, f"{EVENTS}/e1/20261018T090000Z.json", snapshot("2026-10-18T09:00:00Z", event()))
    write(store, f"{EVENTS}/e1/20261018T110000Z.json", snapshot("2026-10-18T11:00:00Z", event()))
    (tmp_path / EVENTS / "empty").mkdir()
    games = logged_games(SCHEDULE, store)
    assert len(games) == 1
    game = games[0]
    assert isinstance(game, LoggedGame)
    assert (game.stamp, game.row, game.event.id) == ("20261018T110000Z", 7, "e1")
    assert game.lines["draftkings"][1] == 44.5


@pytest.mark.parametrize(
    ("home", "kickoff"),
    [("Denver Broncos", "2026-10-18T17:00:00Z"), ("Atlanta Falcons", "2026-11-01T17:00:00Z")],
    ids=["teams-not-on-schedule", "date-too-far"],
)
def test_logged_games_drop_unmatched_events(store: LocalStorage, home: str, kickoff: str) -> None:
    game = event(home=home, kickoff=kickoff, bookmakers=[main_book()])
    write(store, f"{MAIN}/20261018T100000Z.json", snapshot(FETCHED, [game]))
    write(store, f"{EVENTS}/e1/20261018T110000Z.json", snapshot(FETCHED, game))
    assert logged_games(SCHEDULE, store) == []


def test_logged_games_need_main_lines(store: LocalStorage) -> None:
    write(store, f"{EVENTS}/e1/20261018T110000Z.json", snapshot(FETCHED, event()))
    assert logged_games(SCHEDULE, store) == []


def test_closing_events_take_the_last_snapshot_before_kickoff(store: LocalStorage) -> None:
    schedule = SCHEDULE.assign(game_id=["2026_07_CHI_ATL", "2026_07_LAC_KC"])
    write(store, f"{CLOSING}/e1/20261018T171000Z.json", snapshot("2026-10-18T17:10:00Z", event()))  # in-play
    assert closing_events(schedule, store) == {}
    early = snapshot("2026-10-18T16:05:00Z", event(bookmakers=[main_book()]))
    write(store, f"{CLOSING}/e1/20261018T160500Z.json", early)
    found = closing_events(schedule, store)
    assert list(found) == ["2026_07_CHI_ATL"]
    assert found["2026_07_CHI_ATL"].bookmakers[0].key == "draftkings"


def test_closing_events_skip_games_missing_from_the_schedule(store: LocalStorage) -> None:
    missing = snapshot("2026-10-18T16:05:00Z", event(home="Denver Broncos"))
    write(store, f"{CLOSING}/e9/20261018T160500Z.json", missing)
    assert closing_events(SCHEDULE.assign(game_id=["a", "b"]), store) == {}
