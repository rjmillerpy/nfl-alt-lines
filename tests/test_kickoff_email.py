import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from pydantic import SecretStr

from nfl_alt_lines import kickoff_email, strategy
from nfl_alt_lines.common.settings import Settings
from nfl_alt_lines.common.storage import LocalStorage
from nfl_alt_lines.nflverse import GAMES
from nfl_alt_lines.odds.snapshots import VENUES, snapshot_key
from nfl_alt_lines.page import REPORT
from tests.conftest import TEST_RULES
from tests.odds.payloads import event, outcome, snapshot
from tests.test_notify import FakeSmtp

WINDY = replace(TEST_RULES, staking=replace(TEST_RULES.staking, windy_stake=0.004, windy_wind_mph=15.0))
NOW = datetime(2026, 11, 22, 17, 15, tzinfo=UTC)  # 12:15 PM Eastern: 45 minutes before the 1 PM games
REPORT_TEXT = """Run at x

WHAT TO BET THIS WEEK
SUNDAY NOV 22 - 2 games
    SINGLE CHI @ ATL: Under 37.5 at +250 on fanduel, 0.5% of bankroll
    SINGLE MIA @ BUF: Under 40.5 at +300 on draftkings, 0.5% of bankroll

AT A GLANCE
"""
GAMES_CSV = """game_id,season,game_type,week,gameday,weekday,gametime,away_team,home_team,stadium_id,roof,wind,temp
g1,2026,REG,12,2026-11-22,Sunday,13:00,CHI,ATL,ATL00,outdoors,20,40
g2,2026,REG,12,2026-11-22,Sunday,16:25,MIA,BUF,BUF00,outdoors,25,30
"""


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LocalStorage:
    found = LocalStorage(tmp_path)
    found.write(GAMES, GAMES_CSV.encode())
    found.write(REPORT, REPORT_TEXT.encode())
    monkeypatch.setattr(kickoff_email, "load_schedule", lambda store: pd.read_csv(tmp_path / GAMES))
    monkeypatch.setattr(kickoff_email, "add_weather", lambda schedule, now: schedule)
    return found


def settings() -> Settings:
    return Settings(
        smtp_user="sender@example.com", smtp_app_password=SecretStr("pw"), report_email_to=("me@example.com",)
    )


def test_due_kickoffs_only_inside_the_window_and_not_yet_sent() -> None:
    schedule = pd.read_csv(__import__("io").StringIO(GAMES_CSV))
    first = pd.Timestamp("2026-11-22T18:00:00Z")
    assert kickoff_email.due_kickoffs(schedule, NOW, set()) == [first]
    assert kickoff_email.due_kickoffs(schedule, NOW, {first.isoformat()}) == []
    assert kickoff_email.due_kickoffs(schedule, NOW - timedelta(hours=1), set()) == []


def test_model_bets_keep_this_slots_games() -> None:
    assert kickoff_email.model_bets(REPORT_TEXT, ["CHI @ ATL"]) == [
        "SINGLE CHI @ ATL: Under 37.5 at +250 on fanduel, 0.5% of bankroll"
    ]


def test_one_email_per_kickoff_with_the_bets_and_windy_unders(
    store: LocalStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(strategy, "load", lambda: WINDY)
    fetched = NOW - timedelta(minutes=10)
    book = {"key": "novig", "markets": [{"key": "totals", "outcomes": [outcome("Under", -104, 41.5)]}]}
    venues = [event(home="Atlanta Falcons", away="Chicago Bears", bookmakers=[book])]
    store.write(snapshot_key(VENUES, fetched), snapshot(fetched.isoformat(), venues).encode())
    server = FakeSmtp()
    assert kickoff_email.kickoff_emails(settings(), NOW, store, server) == 1
    sent = server.sent[0]
    text = sent.get_content()
    assert sent["Subject"] == "NFL kickoff in about an hour: CHI @ ATL" and sent["To"] == "me@example.com"
    assert "SINGLE CHI @ ATL" in text and "MIA @ BUF" not in text
    assert "WINDY UNDER CHI @ ATL: under 41.5 at -104 on novig (forecast wind 20 mph)" in text
    assert kickoff_email.kickoff_emails(settings(), NOW, store, server) == 0  # marked: never twice


def test_nothing_sent_without_a_bet_or_windy_game(store: LocalStorage) -> None:
    store.write(REPORT, b"Run at x\n\nWHAT TO BET THIS WEEK\n")
    server = FakeSmtp()
    assert kickoff_email.kickoff_emails(settings(), NOW, store, server) == 0
    assert server.sent == [] and store.exists(kickoff_email.SENT_MARK)


def test_without_email_settings_nothing_happens(store: LocalStorage) -> None:
    assert kickoff_email.kickoff_emails(Settings(), NOW, store, FakeSmtp()) == 0


def test_body_says_none_when_windy_is_on_but_the_slot_is_calm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(strategy, "load", lambda: WINDY)
    text = kickoff_email.body(pd.Timestamp("2026-11-22T18:00:00Z"), ["CHI @ ATL"], [], [])
    assert "(Sun 12:00 PM)" in text and "    none\n" in text and "no game in this slot is forecast windy" in text


def test_main_logs_a_failed_send_and_carries_on(
    store: LocalStorage, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    env = tmp_path / ".env"
    env.write_text("SMTP_USER=sender@example.com\nSMTP_APP_PASSWORD=pw\n")
    monkeypatch.setattr(kickoff_email, "datetime", type("Clock", (), {"now": staticmethod(lambda tz: NOW)}))
    with caplog.at_level(logging.ERROR):
        kickoff_email.main(env, store, FakeSmtp(failure=OSError("down")))
    assert "kickoff email not sent" in caplog.text
