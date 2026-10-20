import logging
import smtplib
from collections.abc import Iterator
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest
from pydantic import SecretStr

from nfl_alt_lines import notify
from nfl_alt_lines.common import storage
from nfl_alt_lines.common.settings import Settings
from nfl_alt_lines.common.storage import LocalStorage
from nfl_alt_lines.notify import (
    BETS_SENT_MARK,
    SENT_MARK,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_TIMEOUT_SECONDS,
    Outcome,
    bets,
    fingerprint,
    plan_section,
)
from nfl_alt_lines.page import REPORT

PLAN = """Run at {run}; ledger: data/ledger/predictions.csv

WHAT TO BET THIS WEEK (Sunday afternoon: singles and by 2s at one book; any other flagged game: one single)
THURSDAY OCT 15 - 1 game
  SEA @ DEN (Thu 7:15 PM): SINGLE Under 35.5 at {price} on fanduel, 0.5% of bankroll (needs 33.3%, model {model}%)
  DAL @ GB (Sun 7:20 PM): under flagged (ranked {rank} of 100). Prices come in the 2:15 PM email

AT A GLANCE
SEA @ DEN  {weather}
"""
USER = "sender@example.com"
PASSWORD = "abcd efgh ijkl mnop"  # noqa: S105 - a fake app password for the fake server
NOW = datetime(2026, 10, 15, 9, 15)
SETTING_NAMES = (
    "SMTP_USER",
    "SMTP_APP_PASSWORD",
    "REPORT_EMAIL_TO",
    "REPORT_EMAIL_BETS_TO",
    "REPORT_SUBJECT_PREFIX",
    "BANKROLL",
)
FRIENDS = "friend@example.com, other@example.com"
SINGLE_LINE = "  SEA @ DEN (Thu 7:15 PM): SINGLE Under 35.5 at {price} on fanduel"
NO_BETS = "  SEA @ DEN (Thu 7:15 PM): under flagged (ranked 90 of 100). Prices come in the 2:15 PM email"


def report(run: str = "1", price: str = "+200", model: str = "44.3", rank: str = "99", weather: str = "45F") -> str:
    return PLAN.format(run=run, price=price, model=model, rank=rank, weather=weather)


class FakeSmtp:
    """Stands in for smtplib.SMTP_SSL: records the connection, login and messages; can fail on send (every
    send, or only those to `fail_to`)."""

    def __init__(self, failure: OSError | None = None, fail_to: str | None = None) -> None:
        self.failure = failure
        self.fail_to = fail_to
        self.connections: list[tuple[str, int, float]] = []
        self.logins: list[tuple[str, str]] = []
        self.sent: list[EmailMessage] = []
        self.closed = False

    def __call__(self, host: str, port: int, /, *, timeout: float) -> Self:
        self.connections.append((host, port, timeout))
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None, /
    ) -> None:
        self.closed = True

    def login(self, user: str, password: str, /) -> None:
        self.logins.append((user, password))

    def send_message(self, msg: EmailMessage, /) -> None:
        if self.failure is not None and (self.fail_to is None or self.fail_to == msg["To"]):
            raise self.failure
        self.sent.append(msg)


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"smtp_user": USER, "smtp_app_password": SecretStr(PASSWORD), **overrides}
    return Settings.model_validate(values)


@pytest.fixture
def report_file(tmp_path: Path) -> Path:
    """The latest report as a file inside a local storage rooted at tmp_path (rewritten by some tests)."""
    LocalStorage(tmp_path).write(REPORT, report().encode())
    return tmp_path / REPORT


def send(server: FakeSmtp, report_file: Path, config: Settings | None = None, force: bool = False) -> Outcome:
    store = LocalStorage(report_file.parents[1])
    return notify.notify(config or settings(), NOW, store, server, force)


def test_drift_outside_the_bets_does_not_resend() -> None:
    assert fingerprint(report()) == fingerprint(report(run="2", model="45.1", rank="97", weather="50F, windy"))


def test_a_new_price_resends() -> None:
    assert fingerprint(report()) != fingerprint(report(price="+190"))


def test_report_without_a_plan_ignores_only_the_run_time() -> None:
    assert fingerprint("Run at 1\nno games") == fingerprint("Run at 2\nno games")
    assert fingerprint("Run at 1\nno games") != fingerprint("Run at 1\nweek 7")


def test_calendar_rollover_does_not_resend() -> None:
    base = report()
    assert fingerprint(base.replace("\n\nAT", "\nNo games: Wednesday Oct 14.\n\nAT")) == fingerprint(
        base.replace("\n\nAT", "\nNo games: Wednesday Oct 21.\n\nAT")
    )


def test_plan_section_runs_to_the_end_when_there_is_no_blank_line() -> None:
    assert plan_section("Run at 1\n\nWHAT TO BET\n  a bet") == "WHAT TO BET\n  a bet"


def test_first_send_emails_text_html_and_attachment(report_file: Path) -> None:
    server = FakeSmtp()
    assert send(server, report_file) is Outcome.SENT
    assert server.connections == [(SMTP_HOST, SMTP_PORT, SMTP_TIMEOUT_SECONDS)]
    assert server.logins == [(USER, PASSWORD.replace(" ", ""))]  # Gmail shows app passwords with spaces
    assert server.closed
    (message,) = server.sent
    assert message["Subject"] == "NFL alt lines report Thu Oct 15 09:15"
    assert message["From"] == USER
    assert message["To"] == USER  # nobody else configured: the sender gets it
    body, attachment = message.iter_parts()
    text, page = body.iter_parts()
    assert text.get_content() == report()
    assert page.get_content_type() == "text/html"
    assert "WHAT TO BET THIS WEEK" in page.get_content()
    assert attachment.get_filename() == "nfl-alt-lines.html"


@pytest.mark.parametrize(
    ("overrides", "subject", "to"),
    [
        ({}, "NFL alt lines report Thu Oct 15 09:15", USER),
        (
            {"report_subject_prefix": "Bob's", "report_email_to": "a@example.com, b@example.com"},
            "Bob's NFL alt lines report Thu Oct 15 09:15",
            "a@example.com, b@example.com",
        ),
    ],
    ids=["defaults", "prefix-and-recipients"],
)
def test_subject_prefix_and_recipients(report_file: Path, overrides: dict[str, object], subject: str, to: str) -> None:
    server = FakeSmtp()
    send(server, report_file, settings(**overrides))
    assert server.sent[0]["Subject"] == subject
    assert server.sent[0]["To"] == to


def test_unchanged_plan_is_not_resent_unless_forced(report_file: Path) -> None:
    server = FakeSmtp()
    send(server, report_file)
    report_file.write_text(report(run="2", model="40.0"))  # drift only
    assert send(server, report_file) is Outcome.UNCHANGED
    assert send(server, report_file, force=True) is Outcome.SENT
    report_file.write_text(report(price="+190"))
    assert send(server, report_file) is Outcome.SENT
    assert len(server.sent) == 3


@pytest.mark.parametrize(
    "overrides",
    [{"smtp_user": None}, {"smtp_app_password": None}],
    ids=["no-user", "no-password"],
)
def test_missing_smtp_settings_skip_the_email(report_file: Path, overrides: dict[str, object]) -> None:
    server = FakeSmtp()
    assert send(server, report_file, settings(**overrides)) is Outcome.NOT_CONFIGURED
    assert not server.connections


@pytest.mark.parametrize(
    "failure",
    [
        smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted"),
        smtplib.SMTPRecipientsRefused({"b@example.com": (550, b"no such user")}),
        TimeoutError("timed out"),
    ],
    ids=["bad-login", "refused-recipient", "timeout"],
)
def test_failed_send_is_tried_once_and_not_marked_sent(report_file: Path, failure: OSError) -> None:
    server = FakeSmtp(failure)
    with pytest.raises(notify.EmailSendError, match="sending the report email failed") as raised:
        send(server, report_file)
    assert "example.com" not in str(raised.value)  # addresses stay out of the message (rule 18)
    assert len(server.connections) == 1  # no retry: a retried send can email everyone twice
    assert not report_file.with_name(".last_emailed").exists()  # so the next hourly run tries again


def test_bets_are_the_lines_with_a_bet_to_place_now() -> None:
    assert bets(report()).startswith(SINGLE_LINE.format(price="+200"))
    assert bets(report()).count("\n") == 0  # the "flagged, prices come in ..." line is not a bet
    early = report().replace("model 44.3%)", "model 44.3%). Early price: wait for the 2:15 PM email")
    assert bets(early) == ""
    round_robin = (
        "WHAT TO BET\n  ROUND ROBIN at fanduel only, 3 legs:\n    SINGLE A: x\n    BY 2s: 3 parlays\n    Total"
    )
    assert bets(round_robin).splitlines() == [
        "  ROUND ROBIN at fanduel only, 3 legs:",
        "    SINGLE A: x",
        "    BY 2s: 3 parlays",
    ]


def test_bets_list_gets_its_own_email_only_when_the_bets_change(report_file: Path) -> None:
    server = FakeSmtp()
    config = settings(report_email_bets_to=FRIENDS)
    assert send(server, report_file, config) is Outcome.SENT
    assert [message["To"] for message in server.sent] == [USER, FRIENDS]  # one message each: no shared addresses
    report_file.write_text(report().replace("2:15 PM email", "3:15 PM email"))  # plan changed, bets did not
    send(server, report_file, config)
    assert [message["To"] for message in server.sent[2:]] == [USER]
    report_file.write_text(report(price="+190"))
    send(server, report_file, config)
    assert [message["To"] for message in server.sent[3:]] == [USER, FRIENDS]


def test_bets_list_hears_nothing_without_a_bet_to_place(report_file: Path) -> None:
    server = FakeSmtp()
    report_file.write_text(f"Run at 1\n\nWHAT TO BET THIS WEEK\n{NO_BETS}\n")
    assert send(server, report_file, settings(report_email_bets_to=FRIENDS)) is Outcome.SENT
    assert [message["To"] for message in server.sent] == [USER]
    assert not report_file.with_name(BETS_SENT_MARK.rsplit("/", 1)[1]).exists()


def test_a_failure_on_the_bets_list_retries_only_that_list(report_file: Path) -> None:
    config = settings(report_email_bets_to=FRIENDS)
    failing = FakeSmtp(TimeoutError("timed out"), fail_to=FRIENDS)
    with pytest.raises(notify.EmailSendError):
        send(failing, report_file, config)
    assert [message["To"] for message in failing.sent] == [USER]
    assert report_file.with_name(SENT_MARK.rsplit("/", 1)[1]).exists()
    server = FakeSmtp()
    assert send(server, report_file, config) is Outcome.SENT
    assert [message["To"] for message in server.sent] == [FRIENDS]
    assert send(server, report_file, config) is Outcome.UNCHANGED


@pytest.fixture
def project(tmp_path: Path, report_file: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project directory for main(): the report, the sent mark and a .env with test settings."""
    for name in SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(storage, "default_storage", lambda: LocalStorage(tmp_path))
    monkeypatch.setattr(logging.getLogger(), "handlers", [])  # main() replaces them; put them back afterwards
    env_file = tmp_path / ".env"
    env_file.write_text(f"SMTP_USER={USER}\nSMTP_APP_PASSWORD={PASSWORD}\n")
    return env_file


class Recorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def logged() -> Iterator[list[logging.LogRecord]]:
    """notify's log records; main() replaces the root handlers, so this listens on notify's own logger."""
    logger = logging.getLogger(notify.__name__)
    recorder = Recorder()
    logger.addHandler(recorder)
    yield recorder.records
    logger.removeHandler(recorder)


def as_text(records: list[logging.LogRecord]) -> str:
    """Every message and every piece of context, as a log line would show them."""
    return "\n".join(f"{record.getMessage()} {vars(record)}" for record in records)


def test_main_sends_then_skips_and_logs_no_secrets(project: Path, logged: list[logging.LogRecord]) -> None:
    server = FakeSmtp()
    notify.main([], server, project)
    notify.main([], server, project)
    assert len(server.sent) == 1
    assert [record.getMessage() for record in logged] == ["email sent", "email skipped: betting plan unchanged"]
    assert USER not in as_text(logged) and PASSWORD not in as_text(logged)


def test_main_exits_non_zero_when_the_send_fails(project: Path, logged: list[logging.LogRecord]) -> None:
    server = FakeSmtp(smtplib.SMTPRecipientsRefused({USER: (550, b"no such user")}))
    with pytest.raises(SystemExit) as exited:
        notify.main([], server, project)
    assert exited.value.code == notify.FAILED_EXIT_CODE
    assert logged[0].getMessage() == "email not sent; the next run tries again"
    assert "SMTPRecipientsRefused" in as_text(logged)
    assert USER not in as_text(logged) and PASSWORD not in as_text(logged)


def test_main_exits_non_zero_on_invalid_settings(project: Path) -> None:
    project.write_text("REPORT_EMAIL_TO=not-an-address\n")
    with pytest.raises(SystemExit) as exited:
        notify.main([], FakeSmtp(), project)
    assert exited.value.code == notify.FAILED_EXIT_CODE
