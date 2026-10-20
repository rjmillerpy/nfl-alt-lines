"""The nflverse downloads are served by a fake network from small in-memory frames; files go to tmp_path."""

from pathlib import Path

import pandas as pd
import pytest

from nfl_alt_lines import nflverse
from nfl_alt_lines.common import http_client, storage
from nfl_alt_lines.common.storage import LocalStorage
from nfl_alt_lines.nflverse import (
    GAMES_URL,
    PBP,
    PLAYER_STATS,
    SchemaError,
    load_injuries,
    load_pbp,
    load_player_stats,
    load_schedule,
)
from tests import nfl_fakes

SEASONS = range(2023, 2025)


@pytest.fixture
def network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> nfl_fakes.FakeNflverse:
    games = nfl_fakes.schedule(SEASONS, weeks=2)
    games.loc[0, ["home_team", "away_team"]] = ["OAK", "SD"]
    fake = nfl_fakes.FakeNflverse(games)
    monkeypatch.setattr(storage, "default_storage", lambda: LocalStorage(tmp_path))
    monkeypatch.setattr(http_client, "get", fake.get)
    return fake


def test_schedule_is_downloaded_once_and_old_team_codes_are_updated(network: nfl_fakes.FakeNflverse) -> None:
    first = load_schedule()
    second = load_schedule()
    assert network.urls == [GAMES_URL]
    assert (first.loc[0, "home_team"], first.loc[0, "away_team"]) == ("LV", "LAC")
    pd.testing.assert_frame_equal(first, second)


def test_refresh_downloads_the_schedule_again(network: nfl_fakes.FakeNflverse) -> None:
    load_schedule()
    load_schedule(refresh=True)
    assert network.urls == [GAMES_URL, GAMES_URL]


def test_a_schedule_without_its_key_columns_is_rejected(network: nfl_fakes.FakeNflverse) -> None:
    network.games = network.games.drop(columns=["gameday", "week"])
    with pytest.raises(SchemaError, match="gameday, week"):
        load_schedule()


def test_play_by_play_keeps_only_scrimmage_plays_with_a_value(network: nfl_fakes.FakeNflverse) -> None:
    plays = network.files[PBP.url]
    plays.loc[0, ["pass", "rush"]] = [0.0, 0.0]  # a kickoff or penalty
    plays.loc[1, "epa"] = None
    plays.loc[2, "posteam"] = None
    loaded = load_pbp(SEASONS)
    assert len(loaded) == len(plays) - 3
    assert list(loaded.columns) == list(PBP.columns)


@pytest.mark.parametrize(
    ("refresh_latest", "downloads_on_second_load"),
    [(False, 0), (True, 1)],
    ids=["cached-seasons-are-reused", "refresh-fetches-only-the-latest-season"],
)
def test_season_files_are_cached(
    network: nfl_fakes.FakeNflverse, refresh_latest: bool, downloads_on_second_load: int
) -> None:
    load_player_stats(SEASONS)
    network.urls.clear()
    load_player_stats(SEASONS, refresh_latest=refresh_latest)
    assert network.urls == [PLAYER_STATS.url.format(season=SEASONS[-1])] * downloads_on_second_load


def test_player_stats_use_current_team_codes(network: nfl_fakes.FakeNflverse) -> None:
    stats = load_player_stats(SEASONS)
    assert stats.loc[0, "team"] == "LV"
    assert "SD" not in set(stats["opponent_team"])
    assert len(stats) == 2 * len(network.games)


def test_injury_reports_use_current_team_codes(network: nfl_fakes.FakeNflverse) -> None:
    network.files[nflverse.INJURIES.url].loc[:, "team"] = "STL"
    reports = load_injuries(SEASONS)
    assert set(reports["team"]) == {"LA"}
    assert list(reports.columns) == list(nflverse.INJURIES.columns)


def test_a_failed_refresh_keeps_the_cached_schedule(
    network: nfl_fakes.FakeNflverse, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the old urlretrieve wrote straight into the cache, so a download cut off part-way left a
    truncated file that every later run took for a good cached copy. Now nothing is written unless the whole
    reply arrived; a failed refresh leaves the earlier schedule exactly as it was."""
    first = load_schedule()

    def failing_get(url: str, params: object = None) -> http_client.Response:
        raise http_client.RetriesExhaustedError(url, 3, ConnectionResetError("cut off"))

    monkeypatch.setattr(http_client, "get", failing_get)
    with pytest.raises(http_client.FetchError):
        load_schedule(refresh=True)
    pd.testing.assert_frame_equal(load_schedule(), first)
