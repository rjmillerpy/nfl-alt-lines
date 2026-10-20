"""Logging setup shared by every job (rules 14, 18, 41; roadmap item 21).

Each line is one JSON object: time (UTC), level, logger, event, the job name and a run id, so all lines from
one hourly run can be found together, plus any context passed as `extra={...}`. CloudWatch Logs Insights
reads the fields as they are (`filter job = "report" and level = "error"`).

Jobs keep using the standard `logging` module; structlog only formats. Log events and context only, never
keys, passwords or addresses; as a second line of defence, anything shaped like an email address or a
password inside a URL is masked before a line is written.
"""

import logging
import re
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TextIO

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

RUN_ID_LENGTH = 8
_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
URL_PASSWORD = re.compile(r"(\w[\w+.-]*://[^/\s:@]*):[^/\s@]+@")
MASKED_EMAIL = "[email]"
MASKED_PASSWORD = r"\1:[password]@"  # noqa: S105  # nosec B105 - the replacement text, not a password


def mask(text: str) -> str:
    """`text` with email addresses and URL passwords replaced (passwords first: a URL's `user:pw@host`)."""
    return EMAIL.sub(MASKED_EMAIL, URL_PASSWORD.sub(MASKED_PASSWORD, text))


def _run_fields(job: str, run_id: str) -> Processor:
    def add(_logger: WrappedLogger, _method: str, event: EventDict) -> EventDict:
        event.setdefault("job", job)
        event.setdefault("run_id", run_id)
        return event

    return add


class _MaskingFormatter(structlog.stdlib.ProcessorFormatter):
    """Masks the finished line, so nested context and exception text are covered as well."""

    def format(self, record: logging.LogRecord) -> str:
        return mask(super().format(record))


def _formatter(job: str, run_id: str) -> _MaskingFormatter:
    # Every record comes from the standard library, so the whole chain runs as `foreign_pre_chain`.
    return _MaskingFormatter(
        foreign_pre_chain=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.ExtraAdder(),
            _run_fields(job, run_id),
        ],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,  # a traceback becomes the `exception` field of the same line
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(sort_keys=True, default=str),
        ],
    )


@contextmanager
def timed(stage: str) -> Iterator[None]:
    """Log how long a stage of a job took ("stage finished", with `stage` and `seconds`), even if it fails:
    profiling in production, where the jobs run on a slower machine than the laptop (rules 62-65)."""
    start = time.perf_counter()
    try:
        yield
    finally:
        seconds = round(time.perf_counter() - start, 3)
        logging.getLogger(__name__).info("stage finished", extra={"stage": stage, "seconds": seconds})


def current_run_id() -> str | None:
    """The run id of the latest `configure_logging`, if any."""
    return _run_id.get()


def configure_logging(job: str, stream: TextIO = sys.stderr, level: int = logging.INFO) -> str:
    """Send JSON log lines to `stream` tagged with `job` and a fresh run id, which is returned."""
    run_id = uuid.uuid4().hex[:RUN_ID_LENGTH]
    _run_id.set(run_id)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_formatter(job, run_id))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    logging.captureWarnings(True)  # library warnings (pandas, numpy) become JSON lines too, not bare stderr
    return run_id
