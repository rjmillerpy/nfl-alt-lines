"""A status record for every run of a job (roadmap item 22), in the `runs` table next to the ledgers.

`tracked` runs a job and records its job name, run id, start and end, outcome and counts. Code anywhere inside
the job adds to the counts with `count(...)`; outside a tracked run (tests, one-off scripts) that is a no-op.
The same record is logged as a "run finished" line, so a run is visible even when the database is not.

Recording never changes how a job ends: a failure to record is logged, and the job's own exit code and
exceptions pass through untouched.
"""

import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Literal

from nfl_alt_lines.common.logs import RUN_ID_LENGTH, current_run_id
from nfl_alt_lines.common.settings import SettingsError
from nfl_alt_lines.db import service
from nfl_alt_lines.db.schema import RunRow
from nfl_alt_lines.db.service import LedgerStoreError

Count = Literal["games_priced", "rows_written", "plays_ranked", "emails_sent"]
OK, FAILED = "ok", "failed"
FAILED_EXIT_CODE = 1

log = logging.getLogger(__name__)
_counts: ContextVar[dict[str, int] | None] = ContextVar("run_counts", default=None)


def count(name: Count, value: int = 1) -> None:
    """Add `value` to the running job's `name` count."""
    counts = _counts.get()
    if counts is not None:
        counts[name] = counts.get(name, 0) + value


def _store(row: RunRow) -> None:
    service.record_run(service.default_ledgers(), row)


@contextmanager
def _counting() -> Iterator[dict[str, int]]:
    counts: dict[str, int] = {}
    token = _counts.set(counts)
    try:
        yield counts
    finally:
        _counts.reset(token)


def _exit_code(problem: BaseException) -> int:
    if isinstance(problem, SystemExit):
        return problem.code if isinstance(problem.code, int) else (0 if problem.code is None else FAILED_EXIT_CODE)
    return FAILED_EXIT_CODE


def tracked(
    job: str,
    run: Callable[[], int | None],
    store: Callable[[RunRow], None] = _store,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Run `run` (which returns an exit code, None meaning 0), record it, and return its exit code."""
    started, code, error, run_id = clock(), FAILED_EXIT_CODE, None, current_run_id()
    with _counting() as counts:
        try:
            code = run() or 0
        except BaseException as problem:  # recorded, then re-raised unchanged
            code, error = _exit_code(problem), type(problem).__name__
            raise
        finally:
            ended = clock()
            row = RunRow.model_validate(
                {
                    "job": job,
                    # The log lines' run id (the logger sets up logging itself, so it may only exist by now).
                    "run_id": run_id or current_run_id() or uuid.uuid4().hex[:RUN_ID_LENGTH],
                    "started_at": started.isoformat(),
                    "ended_at": ended.isoformat(),
                    "seconds": round((ended - started).total_seconds(), 3),
                    "outcome": OK if code == 0 else FAILED,
                    "error": error,
                    **counts,
                }
            )
            _record(row, store)
    return code


def _record(row: RunRow, store: Callable[[RunRow], None]) -> None:
    # The record's job and run id win over the line's own: the report's email step sets up logging again.
    log.info("run finished", extra=row.model_dump())
    try:
        store(row)
    except (LedgerStoreError, SettingsError) as problem:  # the database, or the settings to reach it
        log.warning("run status not recorded", extra={"reason": str(problem)})
