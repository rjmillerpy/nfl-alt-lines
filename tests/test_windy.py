from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from nfl_alt_lines import strategy, windy
from nfl_alt_lines.common.storage import LocalStorage
from nfl_alt_lines.odds.schema import SlateSnapshot
from nfl_alt_lines.odds.snapshots import VENUES, snapshot_key
from tests.conftest import TEST_RULES
from tests.odds.payloads import event, outcome, snapshot

# Neutral test values (production's are tested privately).
WINDY = replace(
    TEST_RULES, staking=replace(TEST_RULES.staking, windy_stake=0.004, windy_wind_mph=15.0, windy_note=" - test")
)
NOW = datetime(2026, 11, 22, 16, 0, tzinfo=UTC)  # 11 AM Eastern
KICK = "2026-11-22T18:00:00Z"  # 1 PM Eastern


@pytest.fixture
def rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(strategy, "load", lambda: WINDY)


def schedule(wind: float = 18.0, roof: str = "outdoors", gametime: str = "13:00") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_id": ["g1", "g2"],
            "gameday": ["2026-11-22", "2026-11-22"],
            "gametime": [gametime, "16:25"],
            "away_team": ["CHI", "MIA"],
            "home_team": ["ATL", "BUF"],
            "stadium_id": ["ATL00", "BUF00"],
            "roof": [roof, "outdoors"],
            "wind": [wind, 5.0],
        }
    )


def totals(book: str, under: float, point: float) -> dict[str, object]:
    return {
        "key": book,
        "markets": [{"key": "totals", "outcomes": [outcome("Over", -110, point), outcome("Under", under, point)]}],
    }


def slate(*books: dict[str, object], at: datetime = NOW) -> SlateSnapshot:
    return SlateSnapshot.model_validate_json(snapshot(at.isoformat(), [event(kickoff=KICK, bookmakers=list(books))]))


def test_off_without_a_stake() -> None:
    assert not windy.enabled()
    assert windy.watch_section(schedule(), NOW, None) == []


def test_windy_games_keep_open_air_games_at_or_over_the_threshold(rules: None) -> None:
    games = windy.windy_games(schedule(), NOW, NOW + timedelta(days=1))
    assert games["game"].tolist() == ["CHI @ ATL"] and games["wind"].tolist() == [18.0]
    assert windy.windy_games(schedule(roof="dome"), NOW, NOW + timedelta(days=1)).empty
    assert windy.windy_games(schedule(wind=14.0), NOW, NOW + timedelta(days=1)).empty


def test_best_under_takes_the_highest_total_then_the_best_price_after_fees(rules: None) -> None:
    best = windy.best_under(
        slate(totals("circasports", -110, 44.5), totals("novig", -103, 44.5), totals("draftkings", 120, 46.5)),
        "CHI @ ATL",
    )
    assert best == windy.Quote("novig", 44.5, -103)  # DraftKings limits winners: never quoted
    higher = windy.best_under(slate(totals("novig", -103, 44.5), totals("kalshi", 100, 45.0)), "CHI @ ATL")
    assert higher is not None and higher.book == "kalshi" and higher.price < 100  # Kalshi's fee comes off
    assert windy.best_under(slate(totals("novig", -103, 44.5)), "MIA @ BUF") is None


def test_watch_section_lists_the_week_without_prices(rules: None) -> None:
    lines = windy.watch_section(schedule(), NOW, 10_000)
    assert lines[0].startswith("WINDY UNDERS (experimental but promising") and lines[0].endswith(" - test:")
    assert "CHI @ ATL: forecast wind 18 mph" in lines[1]
    assert lines[-1] == "    Stake when it comes: $40.00 (0.4% of bankroll) on the main-line under."
    assert "none forecast this week" in windy.watch_section(schedule(wind=3.0), NOW, None)[1]


def test_priced_lines_quote_the_best_no_limit_book_or_say_there_is_none(rules: None) -> None:
    games = windy.windy_games(schedule(), NOW, NOW + timedelta(days=1))
    priced = windy.priced_lines(games, slate(totals("circasports", -105, 41.5)), None)
    assert priced == ["    WINDY UNDER CHI @ ATL: under 41.5 at -105 on circa (forecast wind 18 mph), 0.4% of bankroll"]
    assert "no Circa, Novig or Kalshi price yet" in windy.priced_lines(games, None, None)[0]


def test_latest_venues_only_while_recent(tmp_path: Path) -> None:
    store = LocalStorage(tmp_path)
    assert windy.latest_venues(store, NOW) is None
    fetched = NOW - timedelta(minutes=10)
    store.write(snapshot_key(VENUES, fetched), snapshot(fetched.isoformat(), [event(kickoff=KICK)]).encode())
    found = windy.latest_venues(store, NOW)
    assert found is not None and found.fetched_at == fetched
    assert windy.latest_venues(store, NOW + timedelta(hours=3)) is None
