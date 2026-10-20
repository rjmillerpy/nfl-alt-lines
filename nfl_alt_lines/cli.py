"""One entry point for both scheduled jobs: `nfl-alt-lines odds-logger` (hourly at :05) and
`nfl-alt-lines report` (hourly at :15). Any further arguments go to the job. `nfl-alt-lines results` prints
the week's results so far (results.py) and sends nothing.

The report job keeps the previous report in place unless the new one finishes, re-downloads nflverse data at
most every 12 hours, and exits non-zero with the reason logged when it fails.
"""

import contextlib
import io
import logging
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from nfl_alt_lines import run_status
from nfl_alt_lines.common import storage
from nfl_alt_lines.common.logs import configure_logging, timed
from nfl_alt_lines.common.storage import Storage
from nfl_alt_lines.nflverse import GAMES
from nfl_alt_lines.page import REPORT, dated_report

REFRESH_AGE = timedelta(hours=12)
LOCAL_ZONE = ZoneInfo("America/Chicago")  # dated report copies follow the user's calendar, not the host's
COMMANDS = ("odds-logger", "report", "results")
HELP_FLAGS = ("-h", "--help")
SINGLE_THREAD = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS")  # several times faster for the report's fits
FAILED_EXIT_CODE = 1

log = logging.getLogger(__name__)


class UnknownCommandError(Exception):
    def __init__(self) -> None:
        super().__init__(f"usage: nfl-alt-lines {{{','.join(COMMANDS)}}} [job arguments]")


def needs_refresh(written: datetime | None, now: datetime) -> bool:
    """True when the downloaded schedule is missing or older than REFRESH_AGE."""
    return written is None or now - written > REFRESH_AGE


def report_job(
    run_report: Callable[[list[str]], None],
    publish: Callable[[], None],
    store: Storage,
    extra: tuple[str, ...] = (),
) -> int:
    """Write the latest report and the day's copy only once the run has finished, then publish (page and email).

    Returns the exit code. A failed run writes nothing, so the previous report stays in place.
    """
    refresh = needs_refresh(store.modified(GAMES), datetime.now(UTC))
    argv = [*extra, *([] if refresh else ["--no-refresh"])]
    printed = io.StringIO()
    try:
        with contextlib.redirect_stdout(printed), timed("report"):
            run_report(argv)
    # Any failure, even an exit, must leave the previous report in place and make the job exit non-zero.
    except Exception, SystemExit:  # noqa: BLE001
        log.exception("report FAILED; the previous report is kept")
        return FAILED_EXIT_CODE
    text = printed.getvalue().encode()
    store.write(REPORT, text)
    store.write(dated_report(datetime.now(LOCAL_ZONE).date()), text)
    log.info("report written", extra={"refreshed": refresh})
    publish()
    return 0


def _report(argv: list[str]) -> None:
    from nfl_alt_lines import report  # imported here: the logger never loads the model

    report.main(argv)


def _publish() -> None:
    from nfl_alt_lines import notify, page, results

    with timed("page"):
        page.main()
    with timed("email"):
        notify.main([])
    with timed("results email"):
        results.main()


def _results_preview() -> None:
    from nfl_alt_lines import results

    configure_logging("results")
    print(results.preview())  # noqa: T201 - the command's output; it sends nothing


def _odds_logger(argv: list[str]) -> None:
    from nfl_alt_lines.odds import logger

    logger.main(argv)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] and args[0] in HELP_FLAGS:
        print(UnknownCommandError().args[0])  # noqa: T201 - asked for: the usage, not a traceback
        return
    if not args or args[0] not in COMMANDS:
        raise UnknownCommandError
    for name in SINGLE_THREAD:
        os.environ.setdefault(name, "1")
    command, rest = args[0], args[1:]
    if command == "results":
        _results_preview()
        return
    if any(flag in rest for flag in HELP_FLAGS):
        (_odds_logger if command == "odds-logger" else _report)(rest)
        return
    if command == "odds-logger":
        sys.exit(run_status.tracked(command, lambda: _odds_logger(rest)))
    configure_logging("report")
    sys.exit(run_status.tracked(command, lambda: report_job(_report, _publish, storage.default_storage(), tuple(rest))))


if __name__ == "__main__":
    main()
