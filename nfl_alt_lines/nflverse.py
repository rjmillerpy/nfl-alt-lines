"""Download and load free nflverse data.

Files are cached in storage under raw/nflverse/ (see common/storage.py). The schedule is fetched when missing
or when a refresh is asked for; a per-season file is fetched when missing, and the latest season's again when
`refresh_latest` is set. Downloads go through the shared HTTP client.
"""

import io
from dataclasses import dataclass

import pandas as pd

from nfl_alt_lines.common import http_client, storage
from nfl_alt_lines.common.storage import Storage

NFLVERSE = "raw/nflverse"
RELEASES = "https://github.com/nflverse/nflverse-data/releases/download"
GAMES_URL = f"{RELEASES}/schedules/games.csv"
GAMES_FILE = "games.csv"
GAMES = f"{NFLVERSE}/{GAMES_FILE}"
# Play-by-play uses each franchise's current abbreviation for every season.
RELOCATED = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
SCHEDULE_COLUMNS = frozenset({"game_id", "season", "week", "gameday", "home_team", "away_team"})
SCRIMMAGE_FLAG = 1


class SchemaError(Exception):
    def __init__(self, source: str, missing: frozenset[str]) -> None:
        super().__init__(f"{source} is missing columns: {', '.join(sorted(missing))}")


@dataclass(frozen=True)
class SeasonFile:
    """One nflverse release with a file per season: where it is published, where it is cached, what is read."""

    url: str
    folder: str
    name: str
    columns: tuple[str, ...]

    def key(self, season: int) -> str:
        return f"{NFLVERSE}/{self.folder}/{self.name.format(season=season)}"


PBP = SeasonFile(
    url=f"{RELEASES}/pbp/play_by_play_{{season}}.parquet",
    folder="pbp",
    name="play_by_play_{season}.parquet",
    columns=(
        "game_id", "posteam", "defteam", "pass", "rush", "epa", "success", "yards_gained", "air_yards",
        "pass_location", "interception", "fumble_lost", "sack", "qb_hit", "pass_oe", "third_down_converted",
        "third_down_failed", "yardline_100", "fixed_drive", "fixed_drive_result", "score_differential",
    ),
)  # fmt: skip
PLAYER_STATS = SeasonFile(
    url=f"{RELEASES}/stats_player/stats_player_week_{{season}}.parquet",
    folder="players",
    name="stats_player_week_{season}.parquet",
    columns=(
        "player_id", "player_display_name", "position", "season", "week", "game_id", "team", "opponent_team",
        "attempts", "passing_yards", "carries", "rushing_yards", "targets", "receiving_yards", "passing_epa",
        "sacks_suffered",
    ),
)  # fmt: skip
INJURIES = SeasonFile(
    url=f"{RELEASES}/injuries/injuries_{{season}}.parquet",
    folder="injuries",
    name="injuries_{season}.parquet",
    columns=("season", "week", "team", "gsis_id", "position", "report_status"),
)


def _relocate(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    return frame.assign(**{column: frame[column].replace(RELOCATED) for column in columns})


def _fetch(url: str, key: str, store: Storage) -> None:
    """Download `url` into storage at `key`; raises http_client.FetchError and leaves the cached copy alone."""
    store.write(key, http_client.get(url).body)


def load_schedule(refresh: bool = False, store: Storage | None = None) -> pd.DataFrame:
    """Every game, played or not.

    `spread_line` is positive when the home team is favoured; `result` is the
    home margin and `total` the combined score.
    """
    store = store or storage.default_storage()
    if refresh or not store.exists(GAMES):
        _fetch(GAMES_URL, GAMES, store)
    games = pd.read_csv(io.BytesIO(store.read(GAMES)))
    missing = SCHEDULE_COLUMNS - set(games.columns)
    if missing:
        raise SchemaError(GAMES_FILE, frozenset(missing))
    return _relocate(games, ("home_team", "away_team"))


def _load_seasons(release: SeasonFile, seasons: range, refresh_latest: bool, store: Storage | None) -> pd.DataFrame:
    """Every season's file, downloading those not cached yet (and the latest one again if asked)."""
    store = store or storage.default_storage()
    cached = set(store.keys(f"{NFLVERSE}/{release.folder}"))  # one listing, not one check per season
    frames = []
    for season in seasons:
        key = release.key(season)
        if key not in cached or (refresh_latest and season == seasons[-1]):
            _fetch(release.url.format(season=season), key, store)
        frames.append(pd.read_parquet(io.BytesIO(store.read(key)), columns=list(release.columns)))
    return pd.concat(frames, ignore_index=True)


def load_pbp(seasons: range, refresh_latest: bool = False, store: Storage | None = None) -> pd.DataFrame:
    """Scrimmage plays (passes and runs, including sacks and scrambles) for the given seasons."""
    plays = _load_seasons(PBP, seasons, refresh_latest, store)
    is_play = (plays["pass"] == SCRIMMAGE_FLAG) | (plays["rush"] == SCRIMMAGE_FLAG)
    scrimmage = is_play & plays["epa"].notna() & plays["posteam"].notna()
    return plays[scrimmage].reset_index(drop=True)


def load_player_stats(seasons: range, refresh_latest: bool = False, store: Storage | None = None) -> pd.DataFrame:
    """One row per player per game they recorded a stat in."""
    return _relocate(_load_seasons(PLAYER_STATS, seasons, refresh_latest, store), ("team", "opponent_team"))


def load_injuries(seasons: range, refresh_latest: bool = False, store: Storage | None = None) -> pd.DataFrame:
    """Each team's official weekly injury report, one row per listed player."""
    return _relocate(_load_seasons(INJURIES, seasons, refresh_latest, store), ("team",))
