"""The email about an hour before kickoff (the owner's choice, 2026-11-21), sent from the hourly report job.

For the games starting in the next WINDOW, it repeats the model's bets on them from the latest report and prices
the windy unders (windy.py) from the odds logger's newest venue snapshot. Every kickoff time gets one email:
the report runs at :15 every hour, so exactly one run falls inside WINDOW (40 to 100 minutes before kickoff:
12:15 for a 1:00 PM game, 3:15 for 4:25, 7:15 for 8:20, all Eastern). Kickoffs already emailed are marked in
storage. Nothing is sent when those games have no model bet and no windy under. It goes to REPORT_EMAIL_TO only
(the owner's list); the bets-only list is unchanged. Like notify.py, a send is never retried.
"""

import logging
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import pandas as pd

from nfl_alt_lines import windy
from nfl_alt_lines.common import storage
from nfl_alt_lines.common.settings import ENV_FILE, Settings, SettingsError, load_settings
from nfl_alt_lines.common.storage import Storage
from nfl_alt_lines.glance import LOCAL_ZONE
from nfl_alt_lines.nflverse import GAMES, load_schedule
from nfl_alt_lines.notify import BET, EARLY_PRICE, EmailSendError, SmtpConnect, plan_section, send
from nfl_alt_lines.odds.schema import SlateSnapshot
from nfl_alt_lines.page import REPORT, REPORTS
from nfl_alt_lines.weather import add_weather, kickoffs

WINDOW = (timedelta(minutes=40), timedelta(minutes=100))
SENT_MARK = f"{REPORTS}/.kickoff_emailed"  # kickoff times already emailed, one ISO time per line
KEEP_MARKS = 60
SUBJECT = "NFL kickoff in about an hour"
TIME_FORMAT = "%a %-I:%M %p"

log = logging.getLogger(__name__)


def due_kickoffs(schedule: pd.DataFrame, now: datetime, sent: set[str]) -> list[pd.Timestamp]:
    """Kickoff times 40-100 minutes away that have not been emailed yet."""
    kickoff = kickoffs(schedule).dropna()
    soon = kickoff[(kickoff - pd.Timestamp(now) >= WINDOW[0]) & (kickoff - pd.Timestamp(now) < WINDOW[1])]
    return sorted({k for k in soon if k.isoformat() not in sent})


def model_bets(report: str, games: list[str]) -> list[str]:
    """The latest plan's bets to place on these games (the lines notify.py counts as bets)."""
    return [
        line.strip()
        for line in plan_section(report).splitlines()
        if BET.search(line) and EARLY_PRICE not in line and any(game in line for game in games)
    ]


def body(at: pd.Timestamp, games: list[str], bets: list[str], windy_lines: list[str]) -> str:
    local = at.tz_convert(LOCAL_ZONE).strftime(TIME_FORMAT)
    lines = [f"Kickoff about an hour from now ({local}): {', '.join(games)}", "", "MODEL BETS FOR THESE GAMES:"]
    lines += [f"    {bet}" for bet in bets] or ["    none"]
    if windy.enabled():
        lines += ["", windy.heading(), *(windy_lines or ["    none: no game in this slot is forecast windy"])]
    lines += ["", "Check each line and price in the app before betting: they move."]
    return "\n".join(lines)


def message(text: str, settings: Settings, games: list[str]) -> EmailMessage:
    email = EmailMessage()
    subject = f"{SUBJECT}: {', '.join(games)}"
    prefix = settings.report_subject_prefix
    email["Subject"] = f"{prefix} {subject}" if prefix else subject
    email["From"] = settings.smtp_user
    email["To"] = ", ".join(settings.recipients)
    email.set_content(text)
    return email


def _windy_lines(
    starting: pd.DataFrame, at: pd.Timestamp, snapshot: SlateSnapshot | None, bank: float | None
) -> list[str]:
    if not windy.enabled():
        return []
    return windy.priced_lines(windy.windy_games(starting, at, at + timedelta(minutes=1)), snapshot, bank)


def _sent(store: Storage) -> list[str]:
    return store.read(SENT_MARK).decode().split() if store.exists(SENT_MARK) else []


def kickoff_emails(settings: Settings, now: datetime, store: Storage, connect: SmtpConnect = smtplib.SMTP_SSL) -> int:
    """Send the email for each due kickoff time; returns how many were sent."""
    if not settings.smtp_user or settings.smtp_app_password is None or not store.exists(GAMES):
        return 0
    schedule = load_schedule(store=store)
    sent = _sent(store)
    due = due_kickoffs(schedule, now, set(sent))
    if not due:
        return 0
    schedule = add_weather(schedule, pd.Timestamp(now))
    report = store.read(REPORT).decode() if store.exists(REPORT) else ""
    snapshot = windy.latest_venues(store, now) if windy.enabled() else None
    password = settings.smtp_app_password.get_secret_value().replace(" ", "")
    count = 0
    for at in due:
        starting = schedule[kickoffs(schedule) == at]
        games = list(starting["away_team"] + " @ " + starting["home_team"])
        bets = model_bets(report, games)
        windy_lines = _windy_lines(starting, at, snapshot, settings.bankroll)
        if bets or windy_lines:
            send(message(body(at, games, bets, windy_lines), settings, games), settings.smtp_user, password, connect)
            count += 1
        sent.append(at.isoformat())
        store.write(SENT_MARK, "\n".join(sent[-KEEP_MARKS:]).encode())
    log.info("kickoff emails", extra={"sent": count, "kickoffs": len(due)})
    return count


def main(env_file: Path = ENV_FILE, store: Storage | None = None, connect: SmtpConnect = smtplib.SMTP_SSL) -> None:
    """Run from the report job after each report; a failure is logged and never stops the regular email."""
    try:
        kickoff_emails(load_settings(env_file), datetime.now(UTC), store or storage.default_storage(), connect)
    except (SettingsError, EmailSendError) as problem:
        # No traceback (TRY400): the chained low-level error's text can hold addresses.
        log.error("kickoff email not sent", extra={"reason": str(problem)})  # noqa: TRY400
