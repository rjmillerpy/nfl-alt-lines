"""Email the latest report, but only when what to bet has changed since the last email.

Two lists, each with its own sent mark:
- REPORT_EMAIL_TO (comma-separated; defaults to SMTP_USER) gets every change to the plan, the "flagged, prices
  come in the 3:15 AM email" kind included.
- REPORT_EMAIL_BETS_TO gets only the important ones: a bet to place now (a single at today's price, or the round
  robin), and only when those bets change. Each list gets its own message, so neither sees the other's addresses.
  On a game day with no bet for it, that list gets one short "no bets today" note instead (quiet_day.py).

Other settings: SMTP_USER and SMTP_APP_PASSWORD (a Gmail app password) and REPORT_SUBJECT_PREFIX (e.g. a name),
which goes in front of the subject.

Sending is deliberately not retried (rule 40): a send that times out may still have been delivered, and a retry
would then email everyone twice. A failed send is logged and the run exits non-zero; the sent mark is only
written after a successful send, so the next hourly run tries again.
"""

import argparse
import hashlib
import logging
import re
import smtplib
import sys
from datetime import UTC, datetime
from email.message import EmailMessage
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self

from nfl_alt_lines import quiet_day, run_status
from nfl_alt_lines.common import storage
from nfl_alt_lines.common.logs import configure_logging
from nfl_alt_lines.common.settings import ENV_FILE, Settings, SettingsError, load_settings
from nfl_alt_lines.common.storage import Storage
from nfl_alt_lines.nflverse import GAMES, load_schedule
from nfl_alt_lines.page import REPORT, REPORTS, render

SENT_MARK = f"{REPORTS}/.last_emailed"  # fingerprint of the plan last emailed to REPORT_EMAIL_TO
BETS_SENT_MARK = f"{REPORTS}/.last_emailed_bets"  # fingerprint of the bets last emailed to REPORT_EMAIL_BETS_TO
HEARD_MARK = f"{REPORTS}/.bets_list_last_day"  # the last day REPORT_EMAIL_BETS_TO got any email (bets or the note)
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_TIMEOUT_SECONDS = 30
SUBJECT = "NFL alt lines report"
SUBJECT_TIME_FORMAT = "%a %b %d %H:%M"
ATTACHMENT_NAME = "nfl-alt-lines.html"
FAILED_EXIT_CODE = 1

PLAN_START = "WHAT TO BET"
RUN_LINE_START = "Run at "
# Model percentages and rankings drift with every weather or price update, and the no-games line with the
# calendar, without changing what to bet.
DRIFT = re.compile(r", model \d+(\.\d+)?%|\(ranked \d+ of \d+\)|^No games: .*$", re.MULTILINE)
# Plan lines with a bet to place (nfl_alt_lines/plan.py): "SINGLE ...", "ROUND ROBIN at ...", "BY 2s: ...". A
# single priced too early says to wait for the game-day email, so it is not one yet.
BET = re.compile(r"\b(SINGLE|ROUND ROBIN at|BY 2s:)")
EARLY_PRICE = "Early price:"

log = logging.getLogger(__name__)


class SmtpConnection(Protocol):
    """The part of `smtplib.SMTP_SSL` used here, so tests can pass a fake server."""

    def __enter__(self) -> Self: ...

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None, /
    ) -> None: ...

    def login(self, user: str, password: str, /) -> object: ...

    def send_message(self, msg: EmailMessage, /) -> object: ...


class SmtpConnect(Protocol):
    def __call__(self, host: str, port: int, /, *, timeout: float) -> SmtpConnection: ...


class Outcome(Enum):
    SENT = "email sent"
    UNCHANGED = "email skipped: betting plan unchanged"
    NOT_CONFIGURED = "email skipped: SMTP_USER and SMTP_APP_PASSWORD are not set in .env"


class EmailSendError(Exception):
    def __init__(self, problem: OSError) -> None:
        # The error's type and SMTP code only: its text can echo recipient addresses (rule 18).
        code = getattr(problem, "smtp_code", None)
        super().__init__(f"sending the report email failed: {type(problem).__name__}, SMTP code {code}")


def plan_section(report: str) -> str:
    """The betting plan block (up to the first blank line), or the whole report if there is no plan."""
    lines = report.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(PLAN_START)), None)
    if start is None:
        return "\n".join(line for line in lines if not line.startswith(RUN_LINE_START))
    end = next((i for i in range(start, len(lines)) if not lines[i].strip()), len(lines))
    return "\n".join(lines[start:end])


def fingerprint(report: str) -> str:
    """Hash of what to bet, so an email goes out only when a bet, line, price, book or stake changes."""
    return _digest(plan_section(report))


def _digest(text: str) -> str:
    return hashlib.sha256(DRIFT.sub("", text).encode()).hexdigest()


def bets(report: str) -> str:
    """The plan's bets to place now, one per line; empty when there are none."""
    lines = plan_section(report).splitlines()
    return "\n".join(line for line in lines if BET.search(line) and EARLY_PRICE not in line)


def build_message(report: str, settings: Settings, now: datetime, to: tuple[str, ...]) -> EmailMessage:
    """The report as plain text, with the phone-friendly page as the HTML alternative and as an attachment."""
    message = EmailMessage()
    subject = f"{SUBJECT} {now.strftime(SUBJECT_TIME_FORMAT)}"
    prefix = settings.report_subject_prefix
    message["Subject"] = f"{prefix} {subject}" if prefix else subject
    message["From"] = settings.smtp_user
    message["To"] = ", ".join(to)
    message.set_content(report)
    page = render(report)
    message.add_alternative(page, subtype="html")
    # The same page as a file, for opening in a browser where the wide tables scroll properly.
    message.add_attachment(page.encode(), maintype="text", subtype="html", filename=ATTACHMENT_NAME)
    return message


def send(message: EmailMessage, user: str, password: str, connect: SmtpConnect) -> None:
    """One attempt, with a timeout and no retry (see the module docstring)."""
    try:
        with connect(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as server:
            server.login(user, password)
            server.send_message(message)
    except OSError as problem:  # smtplib.SMTPException is an OSError, as are timeouts and refused connections
        raise EmailSendError(problem) from problem


def _is_new(store: Storage, path: str, mark: str, force: bool) -> bool:
    return force or not store.exists(path) or store.read(path).decode() != mark


def notify(
    settings: Settings,
    now: datetime,
    store: Storage,
    connect: SmtpConnect = smtplib.SMTP_SSL,
    force: bool = False,
) -> Outcome:
    """Email each list whose part of the plan changed since its last email (`force` sends anyway).

    Each list's mark is written right after its own send, so a failure on the second list never resends the
    first one; the next run retries only the list that failed.
    """
    if not settings.smtp_user or settings.smtp_app_password is None:
        return Outcome.NOT_CONFIGURED
    run_status.count("emails_sent", 0)
    report = store.read(REPORT).decode()
    to_send = []
    mark = fingerprint(report)
    if _is_new(store, SENT_MARK, mark, force):
        to_send.append((settings.recipients, SENT_MARK, mark))
    placed = bets(report)
    bets_mark = _digest(placed)
    if settings.report_email_bets_to and placed and _is_new(store, BETS_SENT_MARK, bets_mark, force):
        to_send.append((settings.report_email_bets_to, BETS_SENT_MARK, bets_mark))
    password = settings.smtp_app_password.get_secret_value().replace(" ", "")
    for to, path, new_mark in to_send:
        send(build_message(report, settings, now, to), settings.smtp_user, password, connect)
        store.write(path, new_mark.encode())
        if path == BETS_SENT_MARK:
            store.write(HEARD_MARK, quiet_day.today(quiet_day.utc(now)).encode())
        run_status.count("emails_sent")
    noted = _note_quiet_day(settings, now, store, password, connect)
    return Outcome.SENT if to_send or noted else Outcome.UNCHANGED


def _note_quiet_day(settings: Settings, now: datetime, store: Storage, password: str, connect: SmtpConnect) -> bool:
    """The bets list's "no bets today" note, when it is due (quiet_day.py); True if it was sent."""
    if not settings.report_email_bets_to or not store.exists(GAMES):
        return False
    at = quiet_day.utc(now)
    games = quiet_day.todays_games(load_schedule(store=store), at)
    heard = store.read(HEARD_MARK).decode() if store.exists(HEARD_MARK) else None
    if not quiet_day.due(games, at, heard):
        return False
    send(quiet_day.note(games, at, settings), settings.smtp_user or "", password, connect)
    store.write(HEARD_MARK, quiet_day.today(at).encode())
    run_status.count("emails_sent")
    log.info("no-bets note sent")
    return True


def main(
    argv: list[str] | None = None,
    connect: SmtpConnect = smtplib.SMTP_SSL,
    env_file: Path = ENV_FILE,
    store: Storage | None = None,
) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="send even if the report has not changed")
    args = parser.parse_args(argv)
    configure_logging("notify")
    try:
        settings = load_settings(env_file)
        outcome = notify(settings, datetime.now(UTC), store or storage.default_storage(), connect, args.force)
    except (SettingsError, EmailSendError) as problem:
        # No traceback (TRY400): the chained low-level error's text can hold addresses.
        log.error("email not sent; the next run tries again", extra={"reason": str(problem)})  # noqa: TRY400
        sys.exit(FAILED_EXIT_CODE)
    context = {"recipients": len(settings.recipients)} if outcome is Outcome.SENT else {}
    log.info(outcome.value, extra=context)


if __name__ == "__main__":
    main()
