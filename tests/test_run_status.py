import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nfl_alt_lines.common.logs import RUN_ID_LENGTH, configure_logging
from nfl_alt_lines.common.settings import SettingsError, load_settings
from nfl_alt_lines.db.schema import RunRow
from nfl_alt_lines.db.service import LedgerStore, LedgerStoreError, recent_runs
from nfl_alt_lines.run_status import count, tracked

START = datetime(2026, 10, 18, 9, 5, tzinfo=UTC)


def clock(seconds: float = 12.5) -> Callable[[], datetime]:
    """Returns START, then START + seconds."""
    times: Iterator[datetime] = iter([START, START + timedelta(seconds=seconds)])
    return lambda: next(times)


def run_and_keep(job: Callable[[], int | None]) -> tuple[int, list[RunRow]]:
    kept: list[RunRow] = []
    return tracked("odds-logger", job, kept.append, clock()), kept


def test_a_run_records_its_times_outcome_and_counts() -> None:
    def job() -> None:
        count("games_priced", 2)
        count("rows_written", 40)
        count("rows_written", 5)

    code, (row,) = run_and_keep(job)
    assert code == 0
    assert row.job == "odds-logger" and row.outcome == "ok" and row.error is None
    assert (row.started_at, row.ended_at, row.seconds) == (START.isoformat(), "2026-10-18T09:05:12.500000+00:00", 12.5)
    assert (row.games_priced, row.rows_written, row.plays_ranked, row.emails_sent) == (2, 45, None, None)
    assert len(row.run_id) == RUN_ID_LENGTH


def test_a_non_zero_exit_code_is_a_failed_run() -> None:
    code, (row,) = run_and_keep(lambda: 1)
    assert code == 1 and row.outcome == "failed" and row.error is None


@pytest.mark.parametrize(
    ("problem", "outcome"),
    [
        (TimeoutError("odds api"), "failed"),
        (SystemExit(1), "failed"),
        (SystemExit(0), "ok"),
        (SystemExit(None), "ok"),
        (SystemExit("usage"), "failed"),
    ],
    ids=["exception", "exit-1", "exit-0", "exit-none", "exit-message"],
)
def test_an_exception_is_recorded_then_raised_unchanged(problem: BaseException, outcome: str) -> None:
    def job() -> None:
        raise problem

    kept: list[RunRow] = []
    with pytest.raises(type(problem)) as raised:
        tracked("report", job, kept.append, clock())
    assert raised.value is problem
    assert kept[0].outcome == outcome and kept[0].error == type(problem).__name__


def test_counts_outside_a_tracked_run_are_ignored_and_runs_do_not_share_them() -> None:
    count("emails_sent")  # no run: nothing to add to, nothing raised
    _, (first,) = run_and_keep(lambda: count("emails_sent"))
    _, (second,) = run_and_keep(lambda: None)
    assert first.emails_sent == 1 and second.emails_sent is None


def test_runs_are_stored_in_the_database(ledgers: LedgerStore) -> None:
    tracked("report", lambda: count("plays_ranked", 300), clock=clock())
    tracked("report", lambda: 1, clock=clock())
    newest, oldest = recent_runs(ledgers, "report", 5)
    assert (newest.outcome, oldest.outcome, oldest.plays_ranked) == ("failed", "ok", 300)
    assert recent_runs(ledgers, "odds-logger", 5) == []


def test_the_record_shares_the_log_lines_run_id(ledgers: LedgerStore) -> None:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    run_id = configure_logging("report")
    root.handlers, root.level = handlers, level
    tracked("report", lambda: None, clock=clock())
    assert recent_runs(ledgers, "report", 1)[0].run_id == run_id


def bad_settings(folder: Path) -> Exception:
    (folder / ".env").write_text("BANKROLL=lots\n")
    with pytest.raises(SettingsError) as raised:
        load_settings(folder / ".env", environ={})
    return raised.value


@pytest.mark.parametrize(
    "problem",
    [lambda folder: LedgerStoreError("write", "runs"), bad_settings],
    ids=["database", "settings"],
)
def test_a_failure_to_record_never_changes_how_the_job_ends(
    problem: Callable[[Path], Exception], caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    failure = problem(tmp_path)

    def store(row: RunRow) -> None:
        raise failure

    with caplog.at_level(logging.INFO):
        assert tracked("report", lambda: None, store, clock()) == 0
    assert [record.getMessage() for record in caplog.records] == ["run finished", "run status not recorded"]
    assert caplog.records[0].job == "report"  # type: ignore[attr-defined]  # extra fields land on the record
