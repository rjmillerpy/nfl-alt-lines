import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from nfl_alt_lines.common.storage import LocalStorage
from nfl_alt_lines.nflverse import GAMES
from nfl_alt_lines.odds import backfill
from nfl_alt_lines.odds.backfill import (
    SNAPSHOT_COST,
    PastGame,
    ReserveReachedError,
    download_slot,
    history_key,
    main,
    past_games,
)
from nfl_alt_lines.odds.client import HISTORY_API, OddsClient
from nfl_alt_lines.odds.logger import MissingKeyError
from nfl_alt_lines.odds.snapshots import HISTORY, read_event
from tests.odds.network import FakeOddsAPI
from tests.odds.payloads import event

SLOT = datetime(2024, 9, 8, 17, 0, tzinfo=UTC)
WHEN = "2024-09-08T16:00:00Z"
GAMES_CSV = """game_id,season,gameday,gametime,home_team,away_team,result
2024_01_CHI_ATL,2024,2024-09-08,13:00,ATL,CHI,7
2024_01_KC_OAK,2024,2024-09-08,13:00,OAK,KC,-3
2024_01_NOT_PLAYED,2024,2024-09-08,13:00,NE,NYJ,
2023_01_OLD,2023,2023-09-10,13:00,NE,NYJ,3
2024_01_NO_TIME,2024,2024-09-08,,DAL,NYG,10
"""


@pytest.fixture
def store(tmp_path: Path) -> LocalStorage:
    found = LocalStorage(tmp_path / "data")
    found.write(GAMES, GAMES_CSV.encode())
    return found


def history_reply(game: dict[str, object]) -> dict[str, object]:
    return {"timestamp": "2024-09-08T15:59:00Z", "data": game}


def network_for(*games: dict[str, object], credits: int = 1000) -> FakeOddsAPI:
    replies: dict[str, object] = {"/events": {"timestamp": WHEN, "data": list(games)}}
    replies.update({f"/events/{g['id']}/odds": history_reply(g) for g in games})
    return FakeOddsAPI(replies, credits, HISTORY_API)


def test_past_games_keep_finished_games_with_kickoffs_in_utc(store: LocalStorage) -> None:
    found = past_games((2024,), store)
    assert [g.game_id for g in found] == ["2024_01_CHI_ATL", "2024_01_KC_OAK"]
    assert found[0] == PastGame("2024_01_CHI_ATL", "ATL", "CHI", SLOT)
    assert found[1].home == "LV"  # relocated team codes are updated


def test_download_slot_saves_each_game_as_received(store: LocalStorage) -> None:
    game = event("abc", home="Atlanta Falcons", away="Chicago Bears")
    network = network_for(game)
    client = OddsClient(SecretStr("k"), HISTORY_API, network)
    download_slot(client, SLOT, [PastGame("2024_01_CHI_ATL", "ATL", "CHI", SLOT)], 0, store)
    saved = history_key("2024_01_CHI_ATL")
    assert read_event(store, saved).response.id == "abc"
    assert json.loads(store.read(saved))["response"]["sport_key"] == "americanfootball_nfl"  # nothing dropped
    assert network.requests[0][1]["date"] == WHEN


def test_download_slot_skips_games_missing_from_the_history(
    store: LocalStorage, caplog: pytest.LogCaptureFixture
) -> None:
    client = OddsClient(SecretStr("k"), HISTORY_API, network_for())
    download_slot(client, SLOT, [PastGame("2024_01_CHI_ATL", "ATL", "CHI", SLOT)], 0, store)
    assert store.keys(HISTORY) == []
    assert "not in the history" in caplog.text


def test_download_slot_stops_at_the_reserve(store: LocalStorage) -> None:
    client = OddsClient(SecretStr("k"), HISTORY_API, network_for(event("abc"), credits=SNAPSHOT_COST + 10))
    with pytest.raises(ReserveReachedError):
        download_slot(client, SLOT, [PastGame("2024_01_CHI_ATL", "ATL", "CHI", SLOT)], 50, store)


def test_dry_run_spends_nothing(store: LocalStorage, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    network = network_for()
    main(["--seasons", "2024"], tmp_path / "absent", network, store)
    assert "2 games in 1 kickoff slots" in capsys.readouterr().out
    assert network.requests == []


def test_go_without_a_key_stops(store: LocalStorage, tmp_path: Path) -> None:
    with pytest.raises(MissingKeyError):
        main(["--seasons", "2024", "--go"], tmp_path / "absent", network_for(), store)


def test_go_downloads_and_skips_games_already_saved(store: LocalStorage, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ODDS_API_KEY=k\n")
    store.write(history_key("2024_01_KC_OAK"), b"{}")
    network = network_for(event("abc", home="Atlanta Falcons", away="Chicago Bears"))
    main(["--seasons", "2024", "--go"], env_file, network, store)
    assert store.exists(history_key("2024_01_CHI_ATL"))
    assert network.paths == ["/events", "/events/abc/odds"]


def test_main_opens_the_configured_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened = LocalStorage(tmp_path / "data")
    opened.write(GAMES, GAMES_CSV.encode())
    monkeypatch.setattr(backfill, "open_storage", lambda settings: opened)
    main(["--seasons", "2024"], tmp_path / "absent", network_for())
