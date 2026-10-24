from datetime import UTC, datetime

import pandas as pd
import pytest
from pydantic import SecretStr

from nfl_alt_lines.common.settings import Settings
from nfl_alt_lines.quiet_day import due, note, todays_games, utc

# Thursday Oct 22, 2026: one game at 7:15 PM Central (8:15 PM Eastern, as the schedule lists it).
SCHEDULE = pd.DataFrame(
    {
        "gameday": ["2026-10-21", "2026-10-22", "2026-10-25", "2026-10-25"],
        "gametime": ["20:15", "20:15", "13:00", "20:20"],
        "away_team": ["DAL", "NE", "SF", "KC"],
        "home_team": ["NYG", "CHI", "ATL", "SEA"],
    }
)
SIX_FIFTEEN = datetime(2026, 10, 22, 23, 16, tzinfo=UTC)  # 6:16 PM Central: the last run before the 7:15 kickoff
FIVE_FIFTEEN = datetime(2026, 10, 22, 22, 16, tzinfo=UTC)
KICKOFF = datetime(2026, 10, 23, 0, 15, tzinfo=UTC)


def test_todays_games_are_the_owners_calendar_day_in_central_time() -> None:
    games = todays_games(SCHEDULE, SIX_FIFTEEN)
    assert games["game"].tolist() == ["NE @ CHI"]
    assert games["kickoff"].iloc[0].strftime("%H:%M") == "19:15"


def test_late_evening_utc_is_still_today_in_central_time() -> None:
    """00:30 UTC Friday is 7:30 PM Thursday Central: still Thursday's games, not Friday's."""
    assert todays_games(SCHEDULE, datetime(2026, 10, 23, 0, 30, tzinfo=UTC))["game"].tolist() == ["NE @ CHI"]


@pytest.mark.parametrize(
    ("now", "last_heard", "expected"),
    [
        (SIX_FIFTEEN, None, True),
        (SIX_FIFTEEN, "2026-10-21", True),
        (SIX_FIFTEEN, "2026-10-22", False),
        (FIVE_FIFTEEN, None, False),
        (KICKOFF, None, False),
    ],
    ids=["last-run-nothing-sent", "heard-yesterday-only", "bet-or-note-today", "too-early", "at-kickoff"],
)
def test_the_note_is_due_once_at_the_last_run_of_a_day_without_bets(
    now: datetime, last_heard: str | None, expected: bool
) -> None:
    assert due(todays_games(SCHEDULE, now), now, last_heard) is expected


def test_no_note_on_a_day_without_games() -> None:
    saturday = datetime(2026, 10, 24, 23, 16, tzinfo=UTC)
    assert due(todays_games(SCHEDULE, saturday), saturday, None) is False


def test_on_sunday_the_note_waits_for_the_night_game() -> None:
    """Sunday's afternoon kickoffs are not the day's last: the note waits for the night game's last run."""
    afternoon = datetime(2026, 10, 25, 16, 16, tzinfo=UTC)  # 11:16 AM Central, before the noon kickoff
    night = datetime(2026, 10, 26, 0, 16, tzinfo=UTC)  # 7:16 PM Central, the last run before the 7:20 PM kickoff
    assert due(todays_games(SCHEDULE, afternoon), afternoon, None) is False
    assert due(todays_games(SCHEDULE, night), night, None) is True


def test_the_note_names_the_games_and_goes_only_to_the_bets_list() -> None:
    config = Settings.model_validate(
        {
            "smtp_user": "sender@example.com",
            "smtp_app_password": SecretStr("abcd efgh ijkl mnop"),
            "report_email_to": "owner@example.com",
            "report_email_bets_to": "friend@example.com, other@example.com",
            "report_subject_prefix": "Bob's",
        }
    )
    message = note(todays_games(SCHEDULE, SIX_FIFTEEN), SIX_FIFTEEN, config)
    assert message["Subject"] == "Bob's NFL alt lines: no bets today (Thursday Oct 22)"
    assert message["To"] == "friend@example.com, other@example.com"
    body = message.get_content()
    assert "today's 1 game (NE @ CHI 7:15 PM, Central time)" in body
    assert "nothing to place" in body


def test_a_naive_time_is_taken_as_utc() -> None:
    assert utc(datetime(2026, 10, 22, 23, 16)) == SIX_FIFTEEN
    assert utc(SIX_FIFTEEN) is SIX_FIFTEEN
