import os
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nfl_alt_lines import cli
from nfl_alt_lines.cli import REFRESH_AGE, UnknownCommandError, main, needs_refresh, report_job
from nfl_alt_lines.common.storage import LocalStorage, Storage
from nfl_alt_lines.db.service import LedgerStore, recent_runs
from nfl_alt_lines.nflverse import GAMES
from nfl_alt_lines.page import REPORT, REPORTS


@pytest.fixture
def store(tmp_path: Path) -> LocalStorage:
    found = LocalStorage(tmp_path)
    found.write(GAMES, b"game_id\n")
    return found


def run(store: Storage, report: Callable[[list[str]], None], published: list[bool] | None = None) -> int:
    sink = [] if published is None else published
    return report_job(report, lambda: sink.append(True), store)


def test_report_is_written_with_a_dated_copy(store: LocalStorage) -> None:
    published: list[bool] = []
    assert run(store, lambda argv: print("WHAT TO BET\nnothing"), published) == 0
    assert store.read(REPORT) == b"WHAT TO BET\nnothing\n"
    dated = [key for key in store.keys(REPORTS) if key != REPORT]
    assert len(dated) == 1
    assert store.read(dated[0]) == store.read(REPORT)
    assert published == [True]


@pytest.mark.parametrize("failure", [RuntimeError("download failed"), SystemExit(2)], ids=["error", "exit"])
def test_failed_report_keeps_the_previous_one_and_exits_non_zero(
    store: LocalStorage, failure: BaseException, caplog: pytest.LogCaptureFixture
) -> None:
    store.write(REPORT, b"previous report\n")
    published: list[bool] = []

    def broken(argv: list[str]) -> None:
        print("half a report")
        raise failure

    assert run(store, broken, published) == cli.FAILED_EXIT_CODE
    assert store.read(REPORT) == b"previous report\n"
    assert store.keys(REPORTS) == [REPORT]
    assert published == []
    assert "FAILED" in caplog.text


def test_refresh_follows_the_schedule_files_age(store: LocalStorage, tmp_path: Path) -> None:
    now = datetime.now(UTC)
    assert not needs_refresh(now - timedelta(hours=1), now)
    assert needs_refresh(now - REFRESH_AGE - timedelta(minutes=1), now)
    assert needs_refresh(None, now)
    seen: list[list[str]] = []
    run(store, seen.append)
    old = time.time() - REFRESH_AGE.total_seconds() - 60
    os.utime(tmp_path / GAMES, (old, old))
    run(store, seen.append)
    run(LocalStorage(tmp_path / "empty"), seen.append)
    assert seen == [["--no-refresh"], [], []]


@pytest.mark.parametrize("argv", [[], ["serve"]], ids=["no-command", "unknown-command"])
def test_unknown_command(argv: list[str]) -> None:
    with pytest.raises(UnknownCommandError, match="usage: nfl-alt-lines"):
        main(argv)


def test_odds_logger_command_passes_its_arguments_and_records_the_run(
    monkeypatch: pytest.MonkeyPatch, ledgers: LedgerStore
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "_odds_logger", seen.append)
    with pytest.raises(SystemExit) as stopped:
        main(["odds-logger", "--dry-run"])
    assert stopped.value.code == 0
    assert seen == [["--dry-run"]]
    assert os.environ["OMP_NUM_THREADS"] == "1"
    (record,) = recent_runs(ledgers, "odds-logger", 5)
    assert record.outcome == "ok"


@pytest.mark.parametrize("command", ["odds-logger", "report"])
def test_help_skips_the_job_and_records_nothing(
    monkeypatch: pytest.MonkeyPatch, ledgers: LedgerStore, command: str
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "_report", seen.append)
    monkeypatch.setattr(cli, "_odds_logger", seen.append)
    main([command, "--help"])
    assert seen == [["--help"]]
    assert recent_runs(ledgers, command, 5) == []


def test_report_command_exits_with_the_jobs_code(monkeypatch: pytest.MonkeyPatch, ledgers: LedgerStore) -> None:
    calls: list[tuple[str, ...]] = []

    def job(run_report: object, publish: object, store: object, extra: tuple[str, ...] = ()) -> int:
        calls.append(extra)
        return 1

    monkeypatch.setattr(cli, "report_job", job)
    with pytest.raises(SystemExit) as stopped:
        main(["report", "--book", "fanduel"])
    assert stopped.value.code == 1
    assert calls == [("--book", "fanduel")]
    assert [record.outcome for record in recent_runs(ledgers, "report", 5)] == ["failed"]


def test_job_wrappers_call_the_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    from nfl_alt_lines import notify, page, report, results
    from nfl_alt_lines.odds import logger

    seen: list[str] = []
    monkeypatch.setattr(report, "main", lambda argv: seen.append("report"))
    monkeypatch.setattr(logger, "main", lambda argv: seen.append("logger"))
    monkeypatch.setattr(page, "main", lambda: seen.append("page"))
    monkeypatch.setattr(notify, "main", lambda argv: seen.append("notify"))
    monkeypatch.setattr(results, "main", lambda: seen.append("results"))
    cli._report([])
    cli._odds_logger([])
    cli._publish()
    assert seen == ["report", "logger", "page", "notify", "results"]


def test_results_command_prints_the_preview(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from nfl_alt_lines import results

    monkeypatch.setattr(results, "preview", lambda: "Week Tue Oct 13 to Mon Oct 19")
    monkeypatch.setattr(cli, "configure_logging", lambda job: "run")
    main(["results"])
    assert capsys.readouterr().out == "Week Tue Oct 13 to Mon Oct 19\n"


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_prints_the_usage_instead_of_a_traceback(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    """The Docker image's default command is --help; it used to end in an UnknownCommandError traceback."""
    main([flag])
    assert capsys.readouterr().out.startswith("usage: nfl-alt-lines {odds-logger,report,results}")
