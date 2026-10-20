import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

import pandas as pd
import pytest
from pydantic import SecretStr

from nfl_alt_lines import results
from nfl_alt_lines.common.settings import Settings
from nfl_alt_lines.common.storage import LocalStorage
from nfl_alt_lines.db.schema import PREDICTIONS, ROUND_ROBINS
from nfl_alt_lines.db.service import LedgerStore
from nfl_alt_lines.notify import EmailSendError
from nfl_alt_lines.results import SENT_MARK, bets, due, send_results, summary, week_end
from tests.db.test_schema import prediction
from tests.test_notify import PASSWORD, USER, FakeSmtp

pytestmark = pytest.mark.usefixtures("test_rules")  # tests/conftest.py: neutral rules, not production's

THURSDAY = "2026-10-16T00:15:00+00:00"  # Thu Oct 15, 7:15 PM Central
SUNDAY_NOON = "2026-10-18T17:00:00+00:00"
TUESDAY_9_15 = datetime(2026, 10, 20, 14, 15, tzinfo=UTC)  # 9:15 AM Central
EVERYONE, BETS_ONLY = "me@example.com", "friend@example.com"


def single(game_id: str, game: str, kickoff: str, line: float, price: int, book: str = "fanduel") -> dict[str, object]:
    return prediction(game_id=game_id, game=game, kickoff=kickoff, line=line, price=price, book=book,
                      break_even=100 / (price + 100), model=0.4)  # fmt: skip


def leg(game_id: str, line: float, price: int) -> dict[str, object]:
    row = {"slate": "2026-10-18", "book": "betmgm", "game_id": game_id, "game": game_id, "kickoff": SUNDAY_NOON}
    return {**row, "bet_type": "under", "side": "under", "bet": "Under", "line": line, "shift": 8.0, "price": price,
            "break_even": 100 / (price + 100), "model": 0.35, "percentile": 0.9, "role": "worth a look"}  # fmt: skip


def schedule(*finals: tuple[str, int, int]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"game_id": g, "home_score": home, "away_score": away} for g, home, away in finals]
        or [{"game_id": "none", "home_score": None, "away_score": None}]
    )


@pytest.fixture
def week(ledgers: LedgerStore) -> LedgerStore:
    """The week of 2026-10-13: Thursday's single at FanDuel +210 (DraftKings +200 too), and Sunday's round robin of
    three unders at BetMGM, where DraftKings paid more for A."""
    ledgers.save(
        PREDICTIONS,
        pd.DataFrame(
            [
                single("SD", "SEA @ DEN", THURSDAY, 34.5, 210),
                single("SD", "SEA @ DEN", THURSDAY, 34.5, 200, "draftkings"),
                single("A", "A", SUNDAY_NOON, 36.5, 264, "draftkings"),
                single("SD", "SEA @ DEN", THURSDAY, 34.5, 400, "betonlineag"),  # not a book the plan uses
            ]
        ),
    )
    ledgers.save(ROUND_ROBINS, pd.DataFrame([leg("A", 36.5, 250), leg("B", 32.5, 210), leg("C", 61, 220)]))
    return ledgers


FINALS = (("SD", 17, 14), ("A", 20, 26), ("B", 19, 13), ("C", 30, 33))  # SD 31 (under 34.5 won), A 46, B 32, C 63


def test_week_end_is_the_latest_tuesday_midnight_local() -> None:
    assert str(week_end(TUESDAY_9_15)) == "2026-10-20 00:00:00-05:00"
    assert str(week_end(datetime(2026, 10, 20, 4, tzinfo=UTC))) == "2026-10-13 00:00:00-05:00"  # Monday 11 PM
    assert str(week_end(datetime(2026, 10, 25, 17, tzinfo=UTC))) == "2026-10-20 00:00:00-05:00"


def test_every_emailed_bet_is_graded_as_a_share_of_the_bankroll(week: LedgerStore) -> None:
    graded = bets(week, schedule(*FINALS))
    rows = {row["bet"]: row for _, row in graded.iterrows()}
    thursday = rows["SINGLE Under 34.5 at +210 on fanduel"]  # the best price of the two books
    assert (thursday["stake"], round(thursday["profit_share"], 4)) == (0.01, 0.021)  # the test rules' 1% single
    # The round robin single for A went to DraftKings, which paid more, as the email said.
    assert round(rows["ROUND ROBIN SINGLE Under 36.5 at +264 on draftkings"]["profit_share"], 6) == round(
        -0.02 * 0.5 / 3, 6
    )
    assert rows["ROUND ROBIN SINGLE Under 32.5 at +210 on betmgm"]["profit"] == pytest.approx(2.1)
    # By 2s: A lost, B won at +210, C (Under 61, 63 scored) lost: no parlay has two winners.
    assert rows["BY 2s at betmgm: 3 parlays"]["profit_share"] == pytest.approx(-0.02 * 0.5)


def test_a_game_without_a_final_score_is_pending(week: LedgerStore) -> None:
    graded = bets(week, schedule(("SD", 17, 14)))
    assert graded["profit"].isna().sum() == 4  # three round-robin singles and the by 2s
    text = summary(graded, week_end(TUESDAY_9_15))
    assert "pending" in text and "Season so far: 1 bets settled" in text


def test_the_summary_lists_the_week_and_totals_without_any_dollar_amount(week: LedgerStore) -> None:
    text = summary(bets(week, schedule(*FINALS)), week_end(TUESDAY_9_15))
    assert text.startswith("Week Tue Oct 13 to Mon Oct 19, as a share of your bankroll:")
    assert "Thu Oct 15  SEA @ DEN: SINGLE Under 34.5 at +210 on fanduel, stake 1.00%  won +2.10%" in text
    assert "This week: 5 bets settled, staked 3.00% of bankroll" in text
    assert "$" not in text


@pytest.mark.parametrize(
    ("now", "finals", "sent_before", "expected"),
    [
        (datetime(2026, 10, 20, 13, 15, tzinfo=UTC), FINALS, False, None),  # 8:15 AM Tuesday: too early
        (TUESDAY_9_15, FINALS, False, "2026-10-20"),
        (TUESDAY_9_15, FINALS, True, None),  # already sent this week
        (TUESDAY_9_15, FINALS[:1], False, None),  # games still open: wait for the scores
        (datetime(2026, 10, 21, 14, 15, tzinfo=UTC), FINALS[:1], False, "2026-10-20"),  # Wednesday: send anyway
    ],
    ids=["too-early", "tuesday", "already-sent", "waiting-for-scores", "wednesday"],
)
def test_due_once_a_week_after_the_scores(
    week: LedgerStore,
    tmp_path: Path,
    now: datetime,
    finals: tuple[tuple[str, int, int], ...],
    sent_before: bool,
    expected: str | None,
) -> None:
    store = LocalStorage(tmp_path)
    if sent_before:
        store.write(SENT_MARK, b"2026-10-20")
    end = due(store, now, bets(week, schedule(*finals)))
    assert (None if end is None else end.date().isoformat()) == expected


def test_a_week_without_bets_sends_nothing(week: LedgerStore, tmp_path: Path) -> None:
    """Regression: deployed on Monday 2026-10-19, the first run treated Oct 6-12 (no bets yet) as a finished week
    and emailed "No bets this week" at 5:16 AM."""
    monday_5_16 = datetime(2026, 10, 19, 10, 16, tzinfo=UTC)
    assert due(LocalStorage(tmp_path), monday_5_16, bets(week, schedule(*FINALS))) is None


def settings(**overrides: object) -> Settings:
    values = {"smtp_user": USER, "smtp_app_password": SecretStr(PASSWORD), "report_email_to": EVERYONE,
              "report_email_bets_to": BETS_ONLY, **overrides}  # fmt: skip
    return Settings.model_validate(values)


def test_results_go_only_to_the_every_email_list_once(week: LedgerStore, tmp_path: Path) -> None:
    store, server = LocalStorage(tmp_path), FakeSmtp()
    assert send_results(settings(), TUESDAY_9_15, store, week, schedule(*FINALS), server)
    assert not send_results(settings(), TUESDAY_9_15, store, week, schedule(*FINALS), server)
    (message,) = server.sent
    assert message["To"] == EVERYONE  # never the bets-only list
    assert message["Subject"] == "NFL alt lines results through Mon Oct 19"
    assert store.read(SENT_MARK) == b"2026-10-20"


def test_a_failed_send_is_retried_next_run(week: LedgerStore, tmp_path: Path) -> None:
    store = LocalStorage(tmp_path)
    with pytest.raises(EmailSendError):
        send_results(settings(), TUESDAY_9_15, store, week, schedule(*FINALS), FakeSmtp(TimeoutError("slow")))
    assert not store.exists(SENT_MARK)


def test_nothing_is_sent_without_email_settings(week: LedgerStore, tmp_path: Path) -> None:
    server = FakeSmtp()
    unset = settings(smtp_app_password=None)
    assert not send_results(unset, TUESDAY_9_15, LocalStorage(tmp_path), week, schedule(*FINALS), server)
    assert server.connections == []


def test_main_sends_when_due_and_exits_non_zero_when_sending_fails(
    week: LedgerStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalStorage(tmp_path)
    monkeypatch.setattr(results, "load_schedule", lambda store: schedule(*FINALS))
    for name in ("SMTP_USER", "SMTP_APP_PASSWORD", "REPORT_EMAIL_TO", "REPORT_EMAIL_BETS_TO"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"SMTP_USER={USER}\nSMTP_APP_PASSWORD={PASSWORD}\nREPORT_EMAIL_TO={EVERYONE}\n")
    failing = FakeSmtp(smtplib.SMTPRecipientsRefused({EVERYONE: (550, b"no")}))
    with pytest.raises(SystemExit):
        results.main(failing, env_file, store, TUESDAY_9_15)
    server = FakeSmtp()
    results.main(server, env_file, store, TUESDAY_9_15)
    results.main(server, env_file, store, TUESDAY_9_15)  # sent: nothing more this week
    assert len(server.sent) == 1
    assert isinstance(server.sent[0], EmailMessage)


def test_preview_shows_the_week_in_progress_and_sends_nothing(
    week: LedgerStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(results, "load_schedule", lambda store: schedule(("SD", 17, 14)))
    text = results.preview(datetime(2026, 10, 19, 15, tzinfo=UTC))  # Monday morning: the week is still open
    assert text.startswith("Week Tue Oct 13 to Mon Oct 19")
    assert "won +2.10%" in text and "pending" in text


def test_each_bet_shows_its_clv_at_its_own_price(ledgers: LedgerStore) -> None:
    """CLV: the bet's expected return if the no-vig closing probability is right; a round robin leg uses the price
    it locked at, not the ledger's later one."""
    closed = {"close_fair": 0.35, "close_price": 190, "close_books": 4, "clv": 0.0}
    ledgers.save(
        PREDICTIONS,
        pd.DataFrame(
            [{**single("SD", "SEA @ DEN", THURSDAY, 34.5, 210), **closed},
             {**single("A", "A", SUNDAY_NOON, 36.5, 240, "betmgm"), **closed}]
        ),
    )  # fmt: skip
    ledgers.save(ROUND_ROBINS, pd.DataFrame([leg("A", 36.5, 250), leg("B", 32.5, 210), leg("C", 61, 220)]))
    graded = bets(ledgers, schedule(*FINALS))
    text = summary(graded, week_end(TUESDAY_9_15))
    assert "SINGLE Under 34.5 at +210 on fanduel, stake 1.00%  won +2.10%, CLV +8.5%" in text  # 0.35 x 3.10 - 1
    assert "ROUND ROBIN SINGLE Under 36.5 at +250 on betmgm" in text and "CLV +22.5%" in text  # 0.35 x 3.50 - 1
    assert "average CLV +15.5% over 2 bets" in text  # B and C closed without a snapshot: no CLV
