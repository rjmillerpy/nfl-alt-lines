"""The bets-only list's "no bets today" note (the owner's choice, 2026-10-24).

REPORT_EMAIL_BETS_TO only hears about bets, so on a game day without one it heard nothing and could not tell a
quiet day from a broken system. Now, on a game day with no bet emailed to that list, one short note goes out at
the last report run before the day's last kickoff. Days with a bet are unchanged: the bet email is the only one.
"""

from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import pandas as pd

from nfl_alt_lines.common.settings import Settings
from nfl_alt_lines.glance import LOCAL_ZONE

SCHEDULE_ZONE = "America/New_York"  # the schedule lists kickoffs in Eastern time
LAST_CALL = timedelta(hours=1)  # the report runs hourly, so the run in the last hour before kickoff is the last one
DATE_FORMAT = "%Y-%m-%d"
DAY_FORMAT = "%A %b %d"
SUBJECT = "NFL alt lines: no bets today"


def todays_games(schedule: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """The games kicking off today in the owner's time zone: `game` ("AWAY @ HOME") and `kickoff` (local)."""
    kickoff = pd.to_datetime(schedule["gameday"] + " " + schedule["gametime"], errors="coerce")
    local = kickoff.dt.tz_localize(SCHEDULE_ZONE).dt.tz_convert(LOCAL_ZONE)
    today = pd.Timestamp(now).tz_convert(LOCAL_ZONE).date()
    games = pd.DataFrame({"game": schedule["away_team"] + " @ " + schedule["home_team"], "kickoff": local})
    todays: pd.DataFrame = games[games["kickoff"].dt.date == today]
    return todays.sort_values("kickoff").reset_index(drop=True)


def due(games: pd.DataFrame, now: datetime, last_heard: str | None) -> bool:
    """True on the last run before today's last kickoff, unless the bets list already heard from us today.

    `last_heard` is the last day (DATE_FORMAT) that list got any email: a bet, or this note.
    """
    if games.empty:
        return False
    last = games["kickoff"].max()
    if not last - LAST_CALL <= pd.Timestamp(now) < last:
        return False
    return last_heard != today(now)


def today(now: datetime) -> str:
    """The owner's calendar day, as stored in the bets list's mark."""
    return pd.Timestamp(now).tz_convert(LOCAL_ZONE).strftime(DATE_FORMAT)


def _clock(when: pd.Timestamp) -> str:
    return when.strftime("%I:%M %p").lstrip("0")


def note(games: pd.DataFrame, now: datetime, settings: Settings) -> EmailMessage:
    """The short note: which games were checked, and that nothing met the betting rules."""
    day = pd.Timestamp(now).tz_convert(LOCAL_ZONE).strftime(DAY_FORMAT)
    count = len(games)
    listed = ", ".join(
        f"{game} {_clock(kickoff)}" for game, kickoff in zip(games["game"], games["kickoff"], strict=True)
    )
    message = EmailMessage()
    prefix = settings.report_subject_prefix
    subject = f"{SUBJECT} ({day})"
    message["Subject"] = f"{prefix} {subject}" if prefix else subject
    message["From"] = settings.smtp_user
    message["To"] = ", ".join(settings.report_email_bets_to)
    message.set_content(
        f"No bets today ({day}).\n\n"
        f"The model checked today's {count} game{'s' * (count > 1)} ({listed}, Central time) and none met the"
        " betting rules, so there is nothing to place.\n\n"
        "This list gets an email whenever there is a bet to place, and this note on game days without one.\n"
    )
    return message


def utc(now: datetime) -> datetime:
    """`now` as an aware time; a naive one is taken as UTC, the time zone the scheduled jobs run in."""
    return now if now.tzinfo else now.replace(tzinfo=UTC)
