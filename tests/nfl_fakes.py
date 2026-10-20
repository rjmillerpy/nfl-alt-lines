"""Small, deterministic stand-ins for nflverse files, and a fake network that serves them.

Four teams play a round robin each week; results, totals and plays cycle through fixed values so every bet
type has both hits and misses at every shift. Shared by the tests of nflverse, status, features, model and
evaluate; nothing here touches data/ or the network.
"""

import io
import re
from collections.abc import Callable, Mapping

import numpy as np
import pandas as pd

from nfl_alt_lines.common import http_client
from nfl_alt_lines.nflverse import INJURIES, PBP, PLAYER_STATS

TEAMS = ("KC", "DEN", "LV", "LAC")
ROUNDS = (((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2)))
STADIUMS = {"KC": ("KAN00", "Arrowhead", "outdoors"), "DEN": ("DEN00", "Mile High", "outdoors")}
DOME = ("LVX00", "Allegiant", "dome")  # LV and LAC play indoors here
ATS = (-25.0, 3.0, -15.0, 9.0, 22.5, -4.0, 16.0, -1.0, 26.0, -9.0, 2.0, -23.0, 5.0)
TOT = (12.0, -24.0, 5.0, -11.0, 23.0, -2.0, 16.0, -18.0, 2.5, 25.0, -6.0)
SPREADS = (3.0, -6.5, 7.0, -2.5, 10.0)
PLAYS_PER_TEAM = 12
PLAYS_PER_DRIVE = 4
DRIVE_RESULTS = ("Touchdown", "Punt", "Field goal")

Fetch = Callable[..., http_client.Response]


def _game(season: int, week: int, home: str, away: str, number: int) -> dict[str, object]:
    stadium_id, stadium, roof = STADIUMS.get(home, DOME)
    spread = SPREADS[number % len(SPREADS)]
    favourite_price, underdog_price = -180.0, 150.0
    home_favoured = spread > 0
    return {
        "game_id": f"{season}_{week:02d}_{away}_{home}",
        "season": season,
        "week": week,
        "game_type": "REG" if week < 6 else "WC",
        "gameday": (pd.Timestamp(f"{season}-09-07") + pd.Timedelta(weeks=week - 1)).strftime("%Y-%m-%d"),
        "gametime": "20:15" if number % 2 else "13:00",
        "home_team": home,
        "away_team": away,
        "result": spread + ATS[number % len(ATS)],
        "total": 44.0 + TOT[number % len(TOT)],
        "spread_line": spread,
        "total_line": 44.0,
        "home_spread_odds": -105.0 if number % 2 else -115.0,
        "away_spread_odds": -115.0 if number % 2 else -105.0,
        "over_odds": -110.0,
        "under_odds": -110.0,
        "home_moneyline": favourite_price if home_favoured else underdog_price,
        "away_moneyline": underdog_price if home_favoured else favourite_price,
        "home_coach": f"coach_{home}",
        "away_coach": f"coach_{away}",
        "location": "Neutral" if number % 7 == 3 else "Home",
        "home_qb_id": f"qb_{home}_{season % 2}",
        "away_qb_id": f"qb_{away}_{season % 2}",
        "roof": roof,
        "stadium_id": stadium_id,
        "stadium": stadium,
        "temp": np.nan if roof == "dome" else 50.0 + week,
        "wind": np.nan if roof == "dome" else float(week % 4 * 5),
        "div_game": 1,
        "home_rest": 7,
        "away_rest": 7 + number % 3,
    }


def schedule(seasons: range, weeks: int = 6) -> pd.DataFrame:
    """Every team plays every week; the last week is a playoff week."""
    rows: list[dict[str, object]] = []
    for season in seasons:
        for week in range(1, weeks + 1):
            pairs = ROUNDS[week % len(ROUNDS)]
            for first, second in pairs:
                home, away = (TEAMS[first], TEAMS[second]) if week % 2 else (TEAMS[second], TEAMS[first])
                rows.append(_game(season, week, home, away, len(rows)))
    return pd.DataFrame(rows)


def _plays_for(game_id: str, offence: str, defence: str, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    count = PLAYS_PER_TEAM
    is_pass = np.arange(count) % 2 == 0
    epa = rng.normal(0, 1, count)
    drive = np.arange(count) // PLAYS_PER_DRIVE + 1
    return pd.DataFrame(
        {
            "game_id": game_id,
            "posteam": offence,
            "defteam": defence,
            "pass": is_pass.astype(float),
            "rush": (~is_pass).astype(float),
            "epa": epa,
            "success": (epa > 0).astype(float),
            "yards_gained": rng.integers(-2, 30, count).astype(float),
            "air_yards": np.where(is_pass, rng.integers(0, 35, count), np.nan),
            "pass_location": pd.Series(np.where(np.arange(count) % 4 == 0, "middle", "left")).where(is_pass),
            "interception": (rng.random(count) < 0.05).astype(float),
            "fumble_lost": (rng.random(count) < 0.03).astype(float),
            "sack": (is_pass & (rng.random(count) < 0.1)).astype(float),
            "qb_hit": (is_pass & (rng.random(count) < 0.2)).astype(float),
            "pass_oe": np.where(is_pass, rng.normal(0, 10, count), np.nan),
            "third_down_converted": (np.arange(count) % 6 == 1).astype(float),
            "third_down_failed": (np.arange(count) % 6 == 4).astype(float),
            "yardline_100": rng.integers(1, 99, count).astype(float),
            "fixed_drive": drive.astype(float),
            "fixed_drive_result": [DRIVE_RESULTS[(seed + d) % len(DRIVE_RESULTS)] for d in drive],
            "score_differential": rng.integers(-21, 21, count).astype(float),
        }
    )


def plays(games: pd.DataFrame) -> pd.DataFrame:
    """Twelve scrimmage plays per team per game."""
    matchups = zip(games["game_id"].tolist(), games["home_team"].tolist(), games["away_team"].tolist(), strict=True)
    frames = [
        _plays_for(game_id, offence, defence, seed=2 * number + side)
        for number, (game_id, home, away) in enumerate(matchups)
        for side, (offence, defence) in enumerate(((home, away), (away, home)))
    ]
    return pd.concat(frames, ignore_index=True)


def player_stats(games: pd.DataFrame) -> pd.DataFrame:
    """One line per starting quarterback per game."""
    sides = [
        pd.DataFrame(
            {
                "player_id": games[f"{side}_qb_id"],
                "player_display_name": games[f"{side}_qb_id"],
                "position": "QB",
                "season": games["season"],
                "week": games["week"],
                "game_id": games["game_id"],
                "team": games[f"{side}_team"],
                "opponent_team": games[f"{other}_team"],
                "attempts": 30.0,
                "passing_yards": 250.0,
                "carries": 3.0,
                "rushing_yards": 10.0,
                "targets": 0.0,
                "receiving_yards": 0.0,
                "passing_epa": games["result"] / 4 if side == "home" else -games["result"] / 4,
                "sacks_suffered": 2.0,
            }
        )
        for side, other in (("home", "away"), ("away", "home"))
    ]
    return pd.concat(sides, ignore_index=True)


def injuries(games: pd.DataFrame) -> pd.DataFrame:
    """KC lists players every week except the first of each season, when no team files a report."""
    reported = games[(games["week"] > 1) & ((games["home_team"] == "KC") | (games["away_team"] == "KC"))]
    return pd.DataFrame(
        {
            "season": reported["season"].to_numpy(),
            "week": reported["week"].to_numpy(),
            "team": "KC",
            "gsis_id": "p1",
            "position": "WR",
            "report_status": "Out",
        }
    )


def row(frame: pd.DataFrame, **match: object) -> dict[str, float]:
    """The numeric columns of the one row whose columns equal `match`, as plain floats."""
    selected = np.logical_and.reduce([frame[column] == value for column, value in match.items()])
    found = frame[selected].select_dtypes("number")
    assert len(found) == 1, f"{len(found)} rows match {match}"
    return {str(name): float(value) for name, value in found.iloc[0].items()}


def parquet(frame: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    frame.to_parquet(buffer)
    return buffer.getvalue()


def _by_season(frame: pd.DataFrame, season: int) -> pd.DataFrame:
    return frame[frame["season"] == season] if "season" in frame else frame.iloc[0:0]


class FakeNflverse:
    """Answers nflverse download URLs from in-memory frames and records every URL asked for."""

    def __init__(self, games: pd.DataFrame, extra: Mapping[str, pd.DataFrame] | None = None) -> None:
        self.games = games
        self.files = {
            PBP.url: _with_season(plays(games), games),
            PLAYER_STATS.url: player_stats(games),
            INJURIES.url: injuries(games),
            **(extra or {}),
        }
        self.urls: list[str] = []

    def get(self, url: str, params: object = None, **_: object) -> http_client.Response:
        self.urls.append(url)
        if url.endswith(".csv"):
            return http_client.Response(body=self.games.to_csv(index=False).encode(), headers={})
        season = int(re.findall(r"(\d{4})\.parquet$", url)[0])
        template = next(template for template in self.files if template.format(season=season) == url)
        return http_client.Response(body=parquet(_by_season(self.files[template], season)), headers={})


def _with_season(play_rows: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Play-by-play has no season column; one is added here only to split plays into season files."""
    return play_rows.merge(games[["game_id", "season"]], on="game_id")
