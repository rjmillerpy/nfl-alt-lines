"""Small Odds API payloads shared by the odds tests, shaped like the real responses."""

import json
from typing import Any

KICKOFF = "2026-10-18T17:00:00Z"


def outcome(name: str, price: float, point: float, description: str | None = None) -> dict[str, Any]:
    found: dict[str, Any] = {"name": name, "price": price, "point": point}
    if description is not None:
        found["description"] = description
    return found


def event(
    event_id: str = "e1",
    home: str = "Atlanta Falcons",
    away: str = "Chicago Bears",
    kickoff: str = KICKOFF,
    bookmakers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "commence_time": kickoff,
        "home_team": home,
        "away_team": away,
        "bookmakers": bookmakers or [],
        "sport_key": "americanfootball_nfl",  # extra fields in real payloads are ignored
    }


def main_book(key: str = "draftkings", spread: float = -3.5, total: float = 44.5) -> dict[str, Any]:
    return {
        "key": key,
        "markets": [
            {
                "key": "spreads",
                "outcomes": [outcome("Atlanta Falcons", -110, spread), outcome("Chicago Bears", -110, -spread)],
            },
            {"key": "totals", "outcomes": [outcome("Over", -110, total), outcome("Under", -110, total)]},
        ],
    }


def snapshot(fetched_at: str, response: object) -> str:
    return json.dumps({"fetched_at": fetched_at, "response": response})
