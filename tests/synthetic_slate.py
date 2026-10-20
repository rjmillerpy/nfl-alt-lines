"""A tiny synthetic NFL world for the report pipeline's tests: schedule and logged price snapshots.

Five played seasons (2010-2014) are the history; two 2015 week-1 games are upcoming at `NOW` and have
snapshot files (valid against odds/schema.py) at three books. Nothing here touches data/ or the network.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nfl_alt_lines.common.storage import LocalStorage
from nfl_alt_lines.odds.snapshots import EVENTS, MAIN

NOW = datetime(2015, 9, 10, 12, tzinfo=UTC)
STAMP = "20150913T120000Z"  # the logged games' last snapshot, an hour before kickoff
SLATE_STAMP = "20150913T110000Z"
KICKOFF = "2015-09-13T17:00:00Z"
PLAYED_SEASONS = range(2010, 2015)
GAMES_PER_SEASON = 16
TEAMS = ("ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE")
UPCOMING = (  # (home name, away name, event id)
    ("Atlanta Falcons", "Chicago Bears", "event-chi-atl"),
    ("Buffalo Bills", "Carolina Panthers", "event-car-buf"),
)
BOOKS = ("draftkings", "fanduel", "betmgm")


def _played_games() -> list[dict[str, Any]]:
    rng = np.random.default_rng(1)
    games = []
    for season in PLAYED_SEASONS:
        for week in range(1, GAMES_PER_SEASON + 1):
            home, away = rng.choice(TEAMS, size=2, replace=False)
            home_score, away_score = (int(points) for points in rng.integers(3, 42, size=2))
            games.append(
                {
                    "game_id": f"{season}_{week:02d}_{away}_{home}",
                    "season": season,
                    "week": week,
                    "gameday": f"{season}-10-{week + 1:02d}",
                    "gametime": "13:00",
                    "home_team": str(home),
                    "away_team": str(away),
                    "home_score": float(home_score),
                    "away_score": float(away_score),
                    "spread_line": float(rng.choice([-7.0, -3.0, 3.0, 7.0])),
                    "total_line": float(rng.choice([41.0, 44.5, 48.0])),
                }
            )
    return games


def schedule() -> pd.DataFrame:
    """Played games plus the two upcoming ones, with the columns the report reads."""
    upcoming = [
        {
            "game_id": f"2015_01_{away}_{home}",
            "season": 2015,
            "week": 1,
            "gameday": "2015-09-13",
            "gametime": "13:00",
            "home_team": home,
            "away_team": away,
            "home_score": np.nan,
            "away_score": np.nan,
            "spread_line": 3.0,
            "total_line": 44.0,
        }
        for home, away in (("ATL", "CHI"), ("BUF", "CAR"))
    ]
    frame = pd.DataFrame([*_played_games(), *upcoming])
    frame["result"] = frame["home_score"] - frame["away_score"]
    frame["total"] = frame["home_score"] + frame["away_score"]
    frame["roof"] = "outdoors"
    frame["stadium_id"] = frame["home_team"]
    frame["temp"], frame["wind"] = 60.0, 15.0
    return frame


def _outcomes(names_points_prices: list[tuple[str, float, int]]) -> list[dict[str, Any]]:
    return [{"name": name, "point": point, "price": price} for name, point, price in names_points_prices]


def _bookmaker(key: str, home: str, away: str, boost: int) -> dict[str, Any]:
    """Home favored by 3 and a total of 44, with alternate lines 7 and 11 points against the bettor."""
    markets = {
        "spreads": [(home, -3.0, -110), (away, 3.0, -110)],
        "totals": [("Over", 44.0, -110), ("Under", 44.0, -110)],
        "alternate_spreads": [
            (home, -10.0, 250 + boost),
            (away, 10.0, -330),
            (home, -14.0, 450 + boost),
            (away, 14.0, -700),
            (away, -4.0, 210 + boost),
            (home, 4.0, -280),
        ],
        "alternate_totals": [
            ("Over", 51.0, 240 + boost),
            ("Under", 51.0, -320),
            ("Under", 37.0, 230 + boost),
            ("Over", 37.0, -300),
        ],
    }
    return {"key": key, "markets": [{"key": name, "outcomes": _outcomes(rows)} for name, rows in markets.items()]}


def event(home: str, away: str, event_id: str, books: tuple[str, ...] = BOOKS) -> dict[str, Any]:
    bookmakers = [_bookmaker(book, home, away, boost=10 * number) for number, book in enumerate(books)]
    return {"id": event_id, "commence_time": KICKOFF, "home_team": home, "away_team": away, "bookmakers": bookmakers}


def write_snapshots(root: Path) -> LocalStorage:
    """Event and main-line snapshots for the upcoming games, in a storage rooted at `root`."""
    store = LocalStorage(root)
    slate = [event(home, away, event_id) for home, away, event_id in UPCOMING]
    store.write(f"{MAIN}/{SLATE_STAMP}.json", json.dumps({"fetched_at": KICKOFF, "response": slate}).encode())
    for snapshot in slate:
        payload = json.dumps({"fetched_at": KICKOFF, "response": snapshot}).encode()
        store.write(f"{EVENTS}/{snapshot['id']}/{STAMP}.json", payload)
    (root / EVENTS / "empty-folder").mkdir()
    return store
