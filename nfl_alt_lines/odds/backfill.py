"""Download past alternate spread and total prices from The Odds API's history (PAID plans only).

Without --go this only prints what the download would cost in API credits; nothing is fetched or spent.
With --go it takes one snapshot per game, about an hour before kickoff, and stops before the credits left
would fall below --reserve (kept back for the hourly logger). Games already downloaded are skipped, so it
can be re-run. The history of alternate lines starts in the 2023 season.

Run: python -m nfl_alt_lines.odds.backfill --seasons 2023 2024 2025 [--go]
"""

import argparse
import io
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from nfl_alt_lines.common import http_client
from nfl_alt_lines.common.logs import configure_logging
from nfl_alt_lines.common.settings import ENV_FILE, load_settings
from nfl_alt_lines.common.storage import Storage, open_storage
from nfl_alt_lines.nflverse import GAMES
from nfl_alt_lines.odds.books import TEAM_CODES
from nfl_alt_lines.odds.client import COMMON_PARAMS, HISTORY_API, Fetch, OddsClient
from nfl_alt_lines.odds.logger import MissingKeyError
from nfl_alt_lines.odds.schema import HistoricalEvent, HistoricalEvents
from nfl_alt_lines.odds.snapshots import HISTORY

MARKETS = "alternate_spreads,alternate_totals"
EVENT_LIST_COST = 1  # credits for one list of events at a past moment
SNAPSHOT_COST = 20  # credits for one game's snapshot: 10 per market per region
DEFAULT_SEASONS = (2023, 2024, 2025)
DEFAULT_RESERVE = 400
LEAD = timedelta(hours=1)
EASTERN = ZoneInfo("America/New_York")
RELOCATED = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
HISTORY_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PastGame:
    game_id: str
    home: str
    away: str
    kickoff: datetime


class ReserveReachedError(Exception):
    def __init__(self, remaining: int, reserve: int) -> None:
        super().__init__(f"stopped with {remaining} credits left (reserve {reserve}); re-run to continue")


def past_games(seasons: tuple[int, ...], store: Storage) -> list[PastGame]:
    """Finished games in the given seasons with their kickoff time in UTC."""
    games = pd.read_csv(io.BytesIO(store.read(GAMES)), dtype=str, keep_default_na=False)
    finished = games[
        games["season"].astype(int).isin(seasons) & ~games["result"].isin(["", "NA"]) & (games["gametime"] != "")
    ]
    kickoffs = (
        pd.to_datetime(finished["gameday"] + "T" + finished["gametime"]).dt.tz_localize(EASTERN).dt.tz_convert(UTC)
    )
    teams = finished[["home_team", "away_team"]].replace(RELOCATED)
    return [
        PastGame(game_id, home, away, kickoff.to_pydatetime())
        for game_id, home, away, kickoff in zip(
            finished["game_id"], teams["home_team"], teams["away_team"], kickoffs, strict=True
        )
    ]


def download_slot(client: OddsClient, slot: datetime, games: list[PastGame], reserve: int, store: Storage) -> int:
    """Snapshot every game kicking off at `slot`; returns the credits left."""
    when = (slot - LEAD).strftime(HISTORY_TIME_FORMAT)
    body, remaining = client.get("/events", date=when)
    ids = {
        (TEAM_CODES[e.home_team], TEAM_CODES[e.away_team]): e.id
        for e in HistoricalEvents.model_validate_json(body).data
    }
    for game in games:
        if remaining - SNAPSHOT_COST < reserve:
            raise ReserveReachedError(remaining, reserve)
        event_id = ids.get((game.home, game.away))
        if event_id is None:
            log.warning("game not in the history, skipped", extra={"game_id": game.game_id, "at": when})
            continue
        body, remaining = client.get(f"/events/{event_id}/odds", markets=MARKETS, date=when, **COMMON_PARAMS)
        raw = json.loads(body)
        HistoricalEvent.model_validate(raw)  # check the shape, then save every field as received
        payload = json.dumps({"fetched_at": raw["timestamp"], "response": raw["data"]})
        store.write(history_key(game.game_id), payload.encode())
    log.info("slot done", extra={"at": when, "credits_remaining": remaining})
    return remaining


def history_key(game_id: str) -> str:
    return f"{HISTORY}/{game_id}.json"


def main(
    argv: list[str] | None = None,
    env_file: Path = ENV_FILE,
    fetch: Fetch = http_client.get,
    store: Storage | None = None,
) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+", default=list(DEFAULT_SEASONS))
    parser.add_argument("--reserve", type=int, default=DEFAULT_RESERVE, help="credits to leave untouched")
    parser.add_argument("--limit", type=int, help="download at most this many kickoff slots (for a trial run)")
    parser.add_argument("--go", action="store_true", help="actually download (spends credits on a paid plan)")
    args = parser.parse_args(argv)
    configure_logging("backfill")
    settings = load_settings(env_file)
    store = store or open_storage(settings)
    done = set(store.keys(HISTORY))
    todo = [g for g in past_games(tuple(args.seasons), store) if history_key(g.game_id) not in done]
    slots = sorted({g.kickoff for g in todo})
    cost = len(slots) * EVENT_LIST_COST + len(todo) * SNAPSHOT_COST
    print(f"{len(todo)} games in {len(slots)} kickoff slots still to download; about {cost} credits.")
    if not args.go:
        print("Dry run: nothing fetched. Add --go on a paid plan to download.")
        return
    if settings.odds_api_key is None:
        raise MissingKeyError
    client = OddsClient(settings.odds_api_key, HISTORY_API, fetch)
    for slot in slots[: args.limit]:
        download_slot(client, slot, [g for g in todo if g.kickoff == slot], args.reserve, store)


if __name__ == "__main__":
    main()
